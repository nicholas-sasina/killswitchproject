"""DynamoStore semantics and the SQS -> Lambda -> DLQ path, on moto (local AWS emulation)."""
import json
import threading

import boto3
import pytest
from moto import mock_aws

from burn_notify import fixtures, handler
from burn_notify.http import Response
from burn_notify.store import DynamoStore

from conftest import Clock, FakeTransport, make_config, make_processor

TABLE = "burn-notify-state"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=TABLE, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "due", "AttributeType": "S"},
                                  {"AttributeName": "window_end", "AttributeType": "N"}],
            GlobalSecondaryIndexes=[{"IndexName": "due", "Projection": {"ProjectionType": "ALL"},
                                     "KeySchema": [{"AttributeName": "due", "KeyType": "HASH"},
                                                   {"AttributeName": "window_end", "KeyType": "RANGE"}]}])
        yield ddb


def dynamo_store(aws):
    return DynamoStore(TABLE, resource=aws)


def test_claim_lifecycle(aws):
    store = dynamo_store(aws)
    assert store.claim("evt_1", "d1", "slack", 100.0).won
    assert not store.claim("evt_1", "d1", "slack", 101.0).won           # in flight
    store.finish("evt_1", "d1", "retrying", 102.0, category="network_error", retry_after=5)
    claim = store.claim("evt_1", "d1", "slack", 103.0)
    assert claim.won and claim.attempts == 2
    store.finish("evt_1", "d1", "delivered", 104.0, provider_ref="42")
    assert not store.claim("evt_1", "d1", "slack", 999.0).won            # terminal
    row = store.get("evt_1", "d1")
    assert row["status"] == "delivered" and row["attempts"] == 2 and row["first_attempt"] == 100
    assert row["provider_ref"] == "42" and row["failure_category"] == "network_error" and row["ttl"] > 104
    assert [r["destination_id"] for r in store.list_deliveries("evt_1")] == ["d1"]


def test_expired_lease_can_be_reclaimed(aws):
    store = dynamo_store(aws)
    assert store.claim("evt_2", "d1", "slack", 100.0).won
    assert store.claim("evt_2", "d1", "slack", 100.0 + store.lease_seconds + 1).won


def test_concurrent_claims_single_winner(aws):
    store = dynamo_store(aws)
    wins, barrier = [], threading.Barrier(10)

    def worker():
        local = DynamoStore(TABLE, resource=boto3.resource("dynamodb", region_name="us-east-1"))
        barrier.wait()
        if local.claim("evt_3", "d1", "slack", 100.0).won:
            wins.append(1)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(wins) == 1


def test_facts_flags_windows_and_rate(aws):
    store = dynamo_store(aws)
    store.add_fact("agent-a", "rotated", "evt_1", 1.0)
    facts = store.add_fact("agent-a", "rotated", "evt_1", 1.0)  # duplicate is idempotent
    assert facts.rotated == {"evt_1"} and store.facts("agent-a").rotated == {"evt_1"}
    assert store.raise_flag("burn/shared/k", "attempted", 1.0)
    assert store.raise_flag("burn/shared/k", "reported", 1.0)
    assert not store.raise_flag("burn/shared/k", "seen", 1.0)
    for _ in range(3):
        count = store.window_add("d1", "blocked#agent-a", 0, 300, {"x": 1}, 10.0)
    assert count == 3
    assert store.due_windows(100.0) == []
    due = store.due_windows(400.0)
    assert len(due) == 1 and due[0]["count"] == 3 and due[0]["sample"] == {"x": 1}
    assert store.claim_window(due[0]["pk"], 400.0) is not None
    assert store.claim_window(due[0]["pk"], 400.0) is None
    assert store.due_windows(400.0) == []
    assert [store.rate_take("d1", 5, 2, 1.0) for _ in range(3)] == [True, True, False]


def test_processor_on_dynamo_dedups(aws):
    slack = FakeTransport()
    proc = make_processor(store=dynamo_store(aws), transports={"slack": slack})
    event = fixtures.canary_used()
    proc.process_record(fixtures.stream_record(event))
    proc.process_record(fixtures.stream_record(event))
    assert len(slack.calls) == 1


def test_aggregation_on_dynamo(aws):
    clock = Clock()
    slack = FakeTransport()
    proc = make_processor(make_config(telegram=None, discord=None), store=dynamo_store(aws),
                          transports={"slack": slack}, clock=clock)
    for i in range(10):
        proc.process_event(fixtures.blocked(offset_ms=i))
    clock.t += 400
    proc.flush_windows()
    assert len(slack.calls) == 4 and proc.flushed[0][1].aggregated_count == 7


# ---- SQS + DLQ ------------------------------------------------------------------------------
def _queues(sqs, max_receives):
    dlq = sqs.create_queue(QueueName="burn-notify-dlq")["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    queue = sqs.create_queue(QueueName="burn-notify", Attributes={
        "VisibilityTimeout": "0",
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": str(max_receives)})})["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    return queue, arn, dlq


def _poll_like_lambda(sqs, queue, arn, proc, rounds=10):
    """Mimic the Lambda SQS event source: receive, invoke, delete only the successes."""
    responses = []
    for _ in range(rounds):
        msgs = sqs.receive_message(QueueUrl=queue, MaxNumberOfMessages=10,
                                   AttributeNames=["ApproximateReceiveCount"]).get("Messages", [])
        if not msgs:
            break
        records = [{"messageId": m["MessageId"], "receiptHandle": m["ReceiptHandle"], "body": m["Body"],
                    "attributes": m["Attributes"], "eventSourceARN": arn} for m in msgs]
        out = handler.queue_handler({"Records": records}, proc=proc, delay=lambda *a: None)
        responses.append(out)
        failed = {f["itemIdentifier"] for f in out["batchItemFailures"]}
        for m in msgs:
            if m["MessageId"] not in failed:
                sqs.delete_message(QueueUrl=queue, ReceiptHandle=m["ReceiptHandle"])
    return responses


def test_exhausted_retries_land_in_dlq(aws):
    sqs = boto3.client("sqs", region_name="us-east-1")
    queue, arn, dlq = _queues(sqs, max_receives=3)
    slack = FakeTransport(default=Response(503, {}, b""))
    proc = make_processor(make_config(telegram=None, discord=None), store=dynamo_store(aws),
                          transports={"slack": slack}, max_receives=3)
    event = fixtures.canary_used()
    sqs.send_message(QueueUrl=queue, MessageBody=json.dumps(fixtures.stream_record(event)))
    responses = _poll_like_lambda(sqs, queue, arn, proc)
    assert len(responses) == 3 and all(r["batchItemFailures"] for r in responses)
    dead = sqs.receive_message(QueueUrl=dlq).get("Messages", [])
    assert len(dead) == 1 and json.loads(dead[0]["Body"])["dynamodb"]["NewImage"]["event_id"]["S"] == event["event_id"]
    row = proc.store.get(event["event_id"], "slack-sec")
    assert row["status"] == "permanently_failed" and row["attempts"] == 3


def test_successful_message_is_deleted_and_other_destinations_unaffected(aws):
    sqs = boto3.client("sqs", region_name="us-east-1")
    queue, arn, dlq = _queues(sqs, max_receives=5)
    proc = make_processor(store=dynamo_store(aws))
    events = [fixtures.canary_used(offset_ms=i) for i in range(3)]
    for event in events:
        sqs.send_message(QueueUrl=queue, MessageBody=json.dumps(fixtures.stream_record(event)))
    responses = _poll_like_lambda(sqs, queue, arn, proc)
    assert all(not r["batchItemFailures"] for r in responses)
    assert sqs.receive_message(QueueUrl=dlq).get("Messages", []) == []
    assert len(proc.store.list_deliveries()) == 9


def test_handler_sets_retry_after_visibility(aws):
    proc = make_processor(make_config(telegram=None, discord=None),
                          transports={"slack": FakeTransport(default=Response(429, {"retry-after": "40"}, b""))})
    delays = []
    record = {"messageId": "m1", "receiptHandle": "rh", "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:q",
              "attributes": {"ApproximateReceiveCount": "1"}, "body": json.dumps(fixtures.stream_record(fixtures.canary_used()))}
    out = handler.queue_handler({"Records": [record]}, proc=proc, delay=lambda *a: delays.append(a))
    assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert delays == [("rh", "arn:aws:sqs:us-east-1:123456789012:q", 41)]


def test_handler_drops_invalid_and_ignores_modify(aws):
    proc = make_processor()
    bad = {"messageId": "bad", "receiptHandle": "r", "body": "{not json"}
    modify = {"messageId": "mod", "receiptHandle": "r",
              "body": json.dumps(fixtures.stream_record(fixtures.blocked(), event_name="MODIFY"))}
    assert handler.queue_handler({"Records": [bad, modify]}, proc=proc) == {"batchItemFailures": []}
    assert proc.store.list_deliveries() == []


def test_queue_url_from_arn():
    assert handler._queue_url("arn:aws:sqs:us-east-1:123456789012:burn-notify") == \
        "https://sqs.us-east-1.amazonaws.com/123456789012/burn-notify"
