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


def key_activity(agent_id: str, since: datetime | None) -> list[dict]:
    secret_ref = agent_secret_ref(agent_id, "aws_key")
    row = registry.get(secret_ref)
    if row is None:
        return []
    version = int(row["version"])
    keys = [(_key_id(secret_ref, "AWSCURRENT"), version)]
    if version > 1:
        keys.append((_key_id(secret_ref, "AWSPREVIOUS"), version - 1))
    events = []
    for key_id, key_version in keys:
        if key_id:
            events += _lookup(key_id, since, f"{secret_ref}#v{key_version}")
    return sorted(events, key=lambda e: e["ts"])
