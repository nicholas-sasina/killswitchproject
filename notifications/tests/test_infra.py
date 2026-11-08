"""Synth the proposed stack (needs the `infra` dependency group: uv run --group infra pytest)."""
import json
import sys
from pathlib import Path

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Match, Template  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from notifications_stack import BurnNotificationsStack  # noqa: E402

STREAM = "arn:aws:dynamodb:us-east-1:111111111111:table/burn-events/stream/2026-11-08T00:00:00.000"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    cfg = tmp_path_factory.mktemp("cfg") / "config.json"
    cfg.write_text(json.dumps({"destinations": []}))
    app = cdk.App(context={"burn:events_stream_arn": STREAM, "burn:notify_config": str(cfg),
                           "burn:notify_ses_identity": "example.com",
                           "burn:dashboard_origin": "https://burn.example.com"})
    stack = BurnNotificationsStack(app, "BurnNotifications",
                                   env=cdk.Environment(account="111111111111", region="us-east-1"))
    return Template.from_stack(stack)


def test_pipe_filters_inserts_into_queue(template):
    template.has_resource_properties("AWS::Pipes::Pipe", {
        "Source": STREAM,
        "SourceParameters": {"FilterCriteria": {"Filters": [{"Pattern": json.dumps({"eventName": ["INSERT"]})}]},
                             "DynamoDBStreamParameters": Match.object_like({"StartingPosition": "LATEST"})}})


def test_queue_has_dlq_and_encryption(template):
    template.has_resource_properties("AWS::SQS::Queue", {
        "QueueName": "burn-notify", "SqsManagedSseEnabled": True,
        "RedrivePolicy": Match.object_like({"maxReceiveCount": 5})})
    template.has_resource_properties("AWS::SQS::Queue", {"QueueName": "burn-notify-dlq"})


def test_worker_reports_batch_failures(template):
    template.has_resource_properties("AWS::Lambda::EventSourceMapping", {
        "FunctionResponseTypes": ["ReportBatchItemFailures"], "BatchSize": 10})
    template.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "burn_notify.handler.queue_handler", "Runtime": "python3.12",
        "Environment": {"Variables": Match.object_like({"BURN_NOTIFY_ALLOW_ENV_SECRETS": "0",
                                                        "BURN_NOTIFY_MAX_RECEIVES": "5"})}})


def test_state_table_ttl_and_due_index(template):
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "TableName": "burn-notify-state", "TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True}})


def test_least_privilege_no_burn_table_access(template):
    policies = json.dumps(template.find_resources("AWS::IAM::Policy"))
    assert "secret:burn-notify/*" in policies
    assert "identity/example.com" in policies
    for forbidden in ("burn-sessions", "burn-credentials", "burn-exposures", "secret:burn/", "secret:burn-system"):
        assert forbidden not in policies
    for policy in template.find_resources("AWS::IAM::Policy").values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if "burn-events" in json.dumps(stmt.get("Resource")):
                actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
                assert set(actions) <= {"dynamodb:DescribeStream", "dynamodb:GetRecords",
                                        "dynamodb:GetShardIterator"}, actions


def test_alarms(template):
    template.resource_count_is("AWS::CloudWatch::Alarm", 3)
