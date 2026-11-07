"""key-activity: CloudTrail lookups for the current and previous access key."""
import json
from datetime import datetime, timezone

import boto3

import aws
import minter_app

AGENT = "agent-tester"


class FakeCloudTrail:
    """moto has no LookupEvents, so answer from a dict keyed by access key id."""

    def __init__(self, events_by_key):
        self.events_by_key, self.calls = events_by_key, []

    def lookup_events(self, **kwargs):
        self.calls.append(kwargs)
        key_id = kwargs["LookupAttributes"][0]["AttributeValue"]
        return {"Events": self.events_by_key.get(key_id, [])}


def ct_event(name, source, when, ip="203.0.113.7", error=None, bucket=None):
    detail = {"sourceIPAddress": ip, "errorCode": error, "requestParameters": {"bucketName": bucket}}
    return {"EventName": name, "EventSource": source, "EventTime": when,
            "CloudTrailEvent": json.dumps(detail)}


def minter(method, path, body=None, query=None):
    return minter_app.handler({"method": method, "path": path, "body": body or {}, "query": query or {}})


def test_key_activity_covers_current_and_previous_key(monkeypatch):
    minter("POST", f"/agents/{AGENT}/credentials", {"types": ["aws_key"]})
    sm = boto3.client("secretsmanager")
    old = json.loads(sm.get_secret_value(SecretId=f"burn/{AGENT}/aws_key")["SecretString"])
    minter("POST", f"/agents/{AGENT}/rotate", {})
    new = json.loads(sm.get_secret_value(SecretId=f"burn/{AGENT}/aws_key")["SecretString"])

    t1 = datetime(2026, 11, 7, 20, 0, 5, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 7, 20, 1, 0, tzinfo=timezone.utc)
    fake = FakeCloudTrail({
        old["access_key_id"]: [ct_event("GetCallerIdentity", "sts.amazonaws.com", t2, error="AccessDenied")],
        new["access_key_id"]: [ct_event("ListBuckets", "s3.amazonaws.com", t1, bucket="some-bucket")],
    })
    monkeypatch.setattr(aws.cloudtrail(), "lookup_events", fake.lookup_events)

    resp = minter("GET", f"/agents/{AGENT}/key-activity", query={"since": "2026-11-07T19:00:00Z"})
    events = resp["body"]["events"]
    keys = resp["body"]["keys"]
    assert [e["event_name"] for e in events] == ["ListBuckets", "GetCallerIdentity"]  # oldest first
    assert events[0] == {
        "ts": "2026-11-07T20:00:05Z", "key_ref": f"burn/{AGENT}/aws_key#v2",
        "key_hint": new["access_key_id"][-4:], "event_name": "ListBuckets",
        "event_source": "s3.amazonaws.com", "resource": "some-bucket",
        "source_ip": "203.0.113.7", "error_code": None}
    assert events[1]["key_ref"] == f"burn/{AGENT}/aws_key#v1" and events[1]["error_code"] == "AccessDenied"
    assert keys == [
        {"key_ref": f"burn/{AGENT}/aws_key#v1", "key_hint": old["access_key_id"][-4:], "status": "inactive"},
        {"key_ref": f"burn/{AGENT}/aws_key#v2", "key_hint": new["access_key_id"][-4:], "status": "active"}]
    assert all("StartTime" in call for call in fake.calls)
    # never leaks a full key id or secret
    dumped = json.dumps(resp)
    assert old["access_key_id"] not in dumped and new["secret_access_key"] not in dumped


def test_key_activity_without_aws_key_is_empty():
    assert minter("GET", f"/agents/{AGENT}/key-activity")["body"] == {"keys": [], "events": []}


def test_never_burned_agent_has_one_active_key(monkeypatch):
    minter("POST", f"/agents/{AGENT}/credentials", {"types": ["aws_key"]})
    monkeypatch.setattr(aws.cloudtrail(), "lookup_events", FakeCloudTrail({}).lookup_events)
    keys = minter("GET", f"/agents/{AGENT}/key-activity")["body"]["keys"]
    assert [(k["key_ref"], k["status"]) for k in keys] == [(f"burn/{AGENT}/aws_key#v1", "active")]


def test_burned_agent_new_key_is_active_even_with_no_events(monkeypatch):
    minter("POST", f"/agents/{AGENT}/credentials", {"types": ["aws_key"]})
    old = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=f"burn/{AGENT}/aws_key")["SecretString"])
    minter("POST", f"/agents/{AGENT}/rotate", {})
    t = datetime(2026, 11, 7, 20, 0, 5, tzinfo=timezone.utc)
    # only the OLD key has events; status must still come from IAM, not from the events
    fake = FakeCloudTrail({old["access_key_id"]: [ct_event("ListBuckets", "s3.amazonaws.com", t)]})
    monkeypatch.setattr(aws.cloudtrail(), "lookup_events", fake.lookup_events)
    body = minter("GET", f"/agents/{AGENT}/key-activity")["body"]
    assert [(k["key_ref"], k["status"]) for k in body["keys"]] == [
        (f"burn/{AGENT}/aws_key#v1", "inactive"), (f"burn/{AGENT}/aws_key#v2", "active")]
    assert {e["key_ref"] for e in body["events"]} == {f"burn/{AGENT}/aws_key#v1"}
