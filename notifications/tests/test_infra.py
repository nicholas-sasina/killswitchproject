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


def test_alarms_notify_the_topic(template):
    template.resource_count_is("AWS::CloudWatch::Alarm", 4)
    template.has_resource_properties("AWS::SNS::Topic", {"TopicName": "burn-notify-alarms"})
    for alarm in template.find_resources("AWS::CloudWatch::Alarm").values():
        assert alarm["Properties"].get("AlarmActions"), alarm["Properties"]["AlarmName"]
    template.has_resource_properties("AWS::CloudWatch::Alarm", {
        "AlarmName": "burn-notify-permanent-failures", "MetricName": "PermanentFailure",
        "Namespace": "Burn/Notifications"})


def test_forwarded_fields_match_the_parser():
    from burn_notify import events
    import notifications_stack
    assert notifications_stack.FORWARDED == events.FORWARDED


def _render_pipe_template(template_text: str, record: dict) -> str:
    """What EventBridge Pipes does with the input template: quoted <$.x> -> the string value,
    unquoted <$.x> -> the JSON value at that path."""
    import re

    def resolve(path):
        value = record
        for part in path.split(".")[1:]:
            value = value[part]
        return value
    text = re.sub(r'"<(\$[^>]+)>"', lambda m: json.dumps(resolve(m.group(1))), template_text)
    return re.sub(r"<(\$[^>]+)>", lambda m: json.dumps(resolve(m.group(1))), text)


@pytest.mark.parametrize("name", ["canary_used", "blocked", "rotation_failed", "flagged_reported", "dismissed",
                                  "provider_reported", "resumed"])
def test_pipe_projection_keeps_classification_and_drops_sensitive_fields(template, name):
    from burn_notify import fixtures
    from burn_notify.events import parse
    from burn_notify.policy import classify
    pipe = next(iter(template.find_resources("AWS::Pipes::Pipe").values()))
    tmpl = pipe["Properties"]["TargetParameters"]["InputTemplate"]
    event = fixtures.ALL[name]()
    event["actor"] = "person@example.com"
    record = fixtures.stream_record(event)
    body = _render_pipe_template(tmpl, record)
    forwarded = parse({"messageId": "m", "receiptHandle": "r", "body": body})
    assert classify(forwarded).category == classify(parse(record)).category
    for dropped in ("fingerprint", "destination", "encoding", "tool", "actor", "owner_agent_id", "target_agent_id"):
        assert forwarded[dropped] is None
        kept_values = {event[f] for f in ("agent_id", "session_id", "role") if event.get(f)}
        if event.get(dropped) and event[dropped] not in kept_values:
            assert event[dropped] not in body


def test_a_stored_event_row_reaches_the_queue_with_every_forwarded_field(template):
    """A row as Burn's writers store it (burn_contracts.events.for_storage), in the stream's DynamoDB JSON,
    through the Pipe's template: every forwarded path exists, so none of them arrives as an empty string."""
    from boto3.dynamodb.types import TypeSerializer
    from burn_contracts.events import for_storage, make_event
    from burn_notify.events import FORWARDED, parse
    event = make_event(agent_id="agent-worker-abc123", source="egress", action="blocked",
                       session_id="sess_0123456789abcdef", secret_ref="burn/agent-worker-abc123/api_key")
    image = {k: TypeSerializer().serialize(v) for k, v in {"stream": "all", **for_storage(event)}.items()}
    record = {"eventID": "1", "eventName": "INSERT", "eventSource": "aws:dynamodb",
              "dynamodb": {"NewImage": image, "StreamViewType": "NEW_IMAGE"}}
    pipe = next(iter(template.find_resources("AWS::Pipes::Pipe").values()))
    body = _render_pipe_template(pipe["Properties"]["TargetParameters"]["InputTemplate"], record)
    forwarded = parse({"messageId": "m", "receiptHandle": "r", "body": body})
    assert {k: forwarded[k] for k in FORWARDED} == {k: event[k] for k in FORWARDED}
