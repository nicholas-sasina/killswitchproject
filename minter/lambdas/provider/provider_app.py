"""Mock API provider: GET /v1/whoami with 'Authorization: Bearer <key>'.

It accepts only the CURRENT version of a key. The key is looked up by its
exact fingerprint in burn-fingerprints, so this Lambda never stores a key.
A key that was rotated gets 401 and a `rejected` event, which is what the
dashboard shows when a stolen pre-rotation key is replayed.
"""
import json
import logging

import boto3

from burn_contracts import fingerprint as fp
from burn_contracts.events import make_event
from burn_contracts.names import (EVENTS_STREAM, EVENTS_TABLE, FINGERPRINT_KEY_SECRET,
                                  FINGERPRINTS_TABLE)

log = logging.getLogger()
_key_cache: dict = {}


def _fingerprint_key() -> bytes:
    if "key" not in _key_cache:  # fetched once per warm container
        secret = boto3.client("secretsmanager").get_secret_value(SecretId=FINGERPRINT_KEY_SECRET)
        _key_cache["key"] = fp.key_from_hex(secret["SecretString"])
    return _key_cache["key"]


def _reply(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps(body)}


def _bearer(headers: dict) -> str | None:
    value = {k.lower(): v for k, v in (headers or {}).items()}.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _record_rejection(row: dict, digest: str) -> None:
    owner = row["owner_agent_id"]
    event = make_event(
        agent_id="system" if owner == "shared" else owner,
        source="provider", action="rejected",
        secret_ref=row["secret_ref"], fingerprint=digest, secret_type="api_key",
        owner="shared" if owner == "shared" else "agent", owner_agent_id=owner,
        detail=f"rotated key presented (was version {int(row['version'])})",
    )
    boto3.resource("dynamodb").Table(EVENTS_TABLE).put_item(Item={"stream": EVENTS_STREAM, **event})


def handler(event, context=None):
    if event.get("rawPath") != "/v1/whoami":
        return _reply(404, {"error": "not found"})
    token = _bearer(event.get("headers"))
    if token is None:
        return _reply(401, {"error": "missing bearer token"})

    digest = fp.fingerprint(token, _fingerprint_key())
    row = boto3.resource("dynamodb").Table(FINGERPRINTS_TABLE).get_item(
        Key={"fingerprint": digest}).get("Item")
    # Only whole-key rows count: a shingle row is a fragment, never a valid key.
    if not row or row["kind"] != "exact" or row["secret_type"] != "api_key":
        return _reply(401, {"error": "invalid key"})
    if row["status"] == "rotated":
        _record_rejection(row, digest)
        return _reply(401, {"error": "key rotated"})
    return _reply(200, {"agent_id": row["owner_agent_id"], "secret_ref": row["secret_ref"]})
