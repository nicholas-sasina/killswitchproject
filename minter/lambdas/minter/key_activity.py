"""What an agent's AWS keys did, from CloudTrail (the blast-radius source).

Looks up the current AND previous access key: after a burn the old key is the
one an attacker would still be trying. Returns only a 4-character hint of each
key id, never the id or the secret.

CloudTrail LookupEvents covers management events only (IAM, STS, S3 bucket
operations), and events can take several minutes to appear.
"""
import json
from datetime import datetime

import aws
import aws_key
import registry
import vault
from burn_contracts.names import agent_secret_ref

MAX_EVENTS_PER_KEY = 200


def _key_id(secret_ref: str, stage: str) -> str | None:
    raw = vault.get(secret_ref, stage)
    return json.loads(raw)["access_key_id"] if raw else None


def _resource(event: dict, detail: dict) -> str | None:
    if event.get("Resources"):
        return event["Resources"][0].get("ResourceName")
    params = detail.get("requestParameters") or {}
    return params.get("bucketName") or params.get("userName")


def _lookup(access_key_id: str, since: datetime | None, key_ref: str) -> list[dict]:
    kwargs = {"LookupAttributes": [{"AttributeKey": "AccessKeyId", "AttributeValue": access_key_id}]}
    if since:
        kwargs["StartTime"] = since
    rows = []
    while len(rows) < MAX_EVENTS_PER_KEY:
        page = aws.cloudtrail().lookup_events(**kwargs)
        for event in page["Events"]:
            detail = json.loads(event.get("CloudTrailEvent") or "{}")
            rows.append({
                "ts": event["EventTime"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "key_ref": key_ref,
                "key_hint": access_key_id[-4:],
                "event_name": event["EventName"],
                "event_source": event.get("EventSource"),
                "resource": _resource(event, detail),
                "source_ip": detail.get("sourceIPAddress"),
                "error_code": detail.get("errorCode"),
            })
        if "NextToken" not in page:
            break
        kwargs["NextToken"] = page["NextToken"]
    return rows


def _status(user: str, key_id: str) -> str:
    """'active' or 'inactive' as IAM reports it; a key IAM no longer lists counts as inactive."""
    listed = aws.iam().list_access_keys(UserName=user)["AccessKeyMetadata"]
    return next((k["Status"].lower() for k in listed if k["AccessKeyId"] == key_id), "inactive")


def key_activity(agent_id: str, since: datetime | None) -> dict:
    """{"keys": [...], "events": [...]} for the agent's current and previous AWS key.

    Key status comes from IAM, never from the events: right after a burn the new
    key has no events yet but is the one that is active.
    """
    secret_ref = agent_secret_ref(agent_id, "aws_key")
    row = registry.get(secret_ref)
    if row is None:
        return {"keys": [], "events": []}
    version = int(row["version"])
    stages = [("AWSPREVIOUS", version - 1)] if version > 1 else []
    stages.append(("AWSCURRENT", version))
    keys, events = [], []
    for stage, key_version in stages:
        key_id = _key_id(secret_ref, stage)
        if not key_id:
            continue
        key_ref = f"{secret_ref}#v{key_version}"
        keys.append({"key_ref": key_ref, "key_hint": key_id[-4:],
                     "status": _status(aws_key.user_name(agent_id), key_id)})
        events += _lookup(key_id, since, key_ref)
    return {"keys": keys, "events": sorted(events, key=lambda e: e["ts"])}
