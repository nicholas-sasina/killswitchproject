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
    cfg.write_text(json.dumps({"destinations": [
        {"id": "email-secops", "provider": "email", "recipient": "secops@example.com, lead@example.com",
         "options": {"from_address": "burn-alerts@example.com"}}]}))
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
        "FunctionResponseTypes": ["ReportBatchItemFailures"], "BatchSize": 1,
        "ScalingConfig": {"MaximumConcurrency": 2}})
    template.has_resource_properties("AWS::Lambda::Function", {
        "Handler": "burn_notify.handler.queue_handler", "Runtime": "python3.12",
        "Timeout": 60,
        "Environment": {"Variables": Match.object_like({"BURN_NOTIFY_ALLOW_ENV_SECRETS": "0",
                                                        "BURN_NOTIFY_MAX_RECEIVES": "5",
                                                        "BURN_NOTIFY_LEASE_SECONDS": "90"})}})


def test_no_reserved_concurrency(template):
    """Reserving concurrency can fail the deploy on accounts with the default 10-execution limit."""
    for fn in template.find_resources("AWS::Lambda::Function").values():
        assert "ReservedConcurrentExecutions" not in fn["Properties"]


def test_queue_visibility_exceeds_lambda_timeout(template):
    template.has_resource_properties("AWS::SQS::Queue", {"QueueName": "burn-notify", "VisibilityTimeout": 360})


def test_pipe_has_dead_letter_queue(template):
    pipe = next(iter(template.find_resources("AWS::Pipes::Pipe").values()))
    assert "DeadLetterConfig" in pipe["Properties"]["SourceParameters"]["DynamoDBStreamParameters"]


def test_ses_send_restricted_to_sender_and_sandbox_recipients(template):
    statements = [st for p in template.find_resources("AWS::IAM::Policy").values()
                  for st in p["Properties"]["PolicyDocument"]["Statement"] if st["Action"] == "ses:SendEmail"]
    assert statements, "no SES statement"
    for st in statements:
        assert st["Condition"] == {"StringEquals": {"ses:FromAddress": ["burn-alerts@example.com"]}}
        text = json.dumps(st["Resource"])
        assert "identity/example.com" in text and "identity/secops@example.com" in text
        assert "identity/lead@example.com" in text and "identity/*" not in text


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
