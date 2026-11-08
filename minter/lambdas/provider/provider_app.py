"""Mock API provider: GET /v1/whoami with 'Authorization: Bearer <key>'.

It accepts only the CURRENT version of a key. The key is looked up by its
exact fingerprint in burn-fingerprints, so this Lambda never stores a key.
A key that was rotated gets 401 and a `rejected` event, which is what the
dashboard shows when a stolen pre-rotation key is replayed.

A canary (a per-session decoy, see minter/lambdas/minter/canaries.py) ALWAYS gets
401 with the same body as an unknown key, and is handed to canary_trip with its
origin: used from outside Burn (may burn) or through a Burn session (alert only),
as vouched for by the egress proxy's x-burn-session-proof header.
"""
import json
import logging

import boto3
from burn_contracts import fingerprint as fp
from burn_contracts.events import for_storage, make_event
from burn_contracts.names import (EVENTS_STREAM, EVENTS_TABLE, FINGERPRINT_KEY_SECRET,
                                  FINGERPRINTS_TABLE)

import canary_trip
import session_proof
import shared_report

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
    boto3.resource("dynamodb").Table(EVENTS_TABLE).put_item(
        Item={"stream": EVENTS_STREAM, **for_storage(event)})


def _source_ip(event: dict) -> str | None:
    ip = ((event.get("requestContext") or {}).get("http") or {}).get("sourceIp")
    return ip if isinstance(ip, str) and len(ip) <= 45 else None


def _origin(row: dict, event: dict) -> tuple[str, str | None]:
    """outside / own_session / burn_session, from the egress proxy's session proof."""
    via = session_proof.verified_session(event.get("headers"), _fingerprint_key())
    if via is None:
        return "outside", None
    return ("own_session" if via == row.get("session_id") else "burn_session"), via


def _on_canary(row: dict, digest: str, event: dict) -> dict:
    """Whatever happens while recording, a canary never authenticates."""
    if row.get("kind") == "exact":
        try:
            origin, via = _origin(row, event)
            canary_trip.on_presented(row, digest, _source_ip(event), origin=origin, via_session=via)
        except Exception as exc:
            log.error("recording canary presentation failed: %s", type(exc).__name__)
    return _reply(401, {"error": "invalid key"})


def handler(event, context=None):
    if event.get("rawPath") != "/v1/whoami":
        return _reply(404, {"error": "not found"})
    token = _bearer(event.get("headers"))
    if token is None:
        return _reply(401, {"error": "missing bearer token"})

    digest = fp.fingerprint(token, _fingerprint_key())
    row = boto3.resource("dynamodb").Table(FINGERPRINTS_TABLE).get_item(
        Key={"fingerprint": digest}).get("Item")
    if row and row.get("canary"):
        return _on_canary(row, digest, event)
    # Only whole-key rows count: a shingle row is a fragment, never a valid key.
    if not row or row["kind"] != "exact" or row["secret_type"] != "api_key":
        return _reply(401, {"error": "invalid key"})
    if row["status"] == "rotated":
        _record_rejection(row, digest)
        return _reply(401, {"error": "key rotated"})
    if row["owner_agent_id"] == "shared":
        shared_report.maybe_report(row, digest)
    return _reply(200, {"agent_id": row["owner_agent_id"], "secret_ref": row["secret_ref"]})
