"""Someone presented a canary to the provider: record it once, hand the incident on.

This is what makes detection independent of how the canary escaped (gzip,
base64, split across messages...): whoever ends up USING it has to send it here
in recognisable form, and its fingerprint row says which session it was planted in.

Bounded and idempotent, so an attacker replaying a copied canary cannot flood
anything: one conditional update on the canary's burn-credentials row counts
every presentation, and only the first one writes a `canary_used` event and
dispatches the incident. A dispatch that failed is retried by the next
presentation, claimed with another conditional update so only one request retries. The Burn API decides whether the incident is actionable
(contracts/README.md, trusted-signal policy); the provider never revokes anything.
"""
import logging

import boto3
from burn_contracts.events import make_event, utc_now_iso
from burn_contracts.names import CREDENTIALS_TABLE, EVENTS_STREAM, EVENTS_TABLE, REGION, is_agent_id

import dispatch

log = logging.getLogger()


def _table(name: str):
    return boto3.resource("dynamodb", region_name=REGION).Table(name)


def _put_event(event: dict) -> None:
    _table(EVENTS_TABLE).put_item(Item={"stream": EVENTS_STREAM, **event})


def _count_presentation(secret_ref: str, event_id: str) -> dict | None:
    """Bump the counter; the first presentation also pins the incident's event id.
    Returns the row as it was BEFORE this presentation, or None if the canary has no registry row."""
    table = _table(CREDENTIALS_TABLE)
    try:
        resp = table.update_item(
            Key={"secret_ref": secret_ref},
            UpdateExpression=(
                "SET first_presented_at = if_not_exists(first_presented_at, :now), last_presented_at = :now, "
                "incident_event_id = if_not_exists(incident_event_id, :evt), #s = :tripped "
                "ADD presentations :one"),
            ConditionExpression="attribute_exists(secret_ref) AND secret_type = :canary",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":now": utc_now_iso(), ":evt": event_id, ":tripped": "tripped",
                                       ":one": 1, ":canary": "canary"},
            ReturnValues="ALL_OLD",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return None
    return resp.get("Attributes", {})


def _mark_dispatch_failed(secret_ref: str) -> None:
    _table(CREDENTIALS_TABLE).update_item(
        Key={"secret_ref": secret_ref}, UpdateExpression="SET dispatch_failed = :t",
        ExpressionAttributeValues={":t": True})


def _claim_retry(secret_ref: str) -> bool:
    """True for exactly one request after a failed dispatch."""
    table = _table(CREDENTIALS_TABLE)
    try:
        table.update_item(Key={"secret_ref": secret_ref}, UpdateExpression="REMOVE dispatch_failed",
                          ConditionExpression="dispatch_failed = :t", ExpressionAttributeValues={":t": True})
        return True
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def _event(row: dict, digest: str, source_ip: str | None, detail: str) -> dict:
    owner = row.get("owner_agent_id")
    return make_event(
        agent_id=owner if is_agent_id(owner) else "system", source="provider", action="canary_used",
        session_id=row.get("session_id"),
        secret_ref=row["secret_ref"], fingerprint=digest, secret_type="other",
        owner="agent" if is_agent_id(owner) else "unknown", owner_agent_id=owner if is_agent_id(owner) else None,
        detail=detail + (f" (from {source_ip})" if source_ip else ""),
    )


def on_presented(row: dict, digest: str, source_ip: str | None = None) -> dict:
    """row: the canary's exact burn-fingerprints row. The caller always answers 401."""
    ref = row["secret_ref"]
    event = _event(row, digest, source_ip,
                   f"canary of session {row.get('session_id')} presented at the provider; rejected")
    before = _count_presentation(ref, event["event_id"])
    if before is None:
        # Fingerprint without a registry row (half-deleted reset): attribution is incomplete,
        # so record it for a human and never contain anything.
        _put_event(_event(row, digest, source_ip, "canary presented but it has no registry row; not contained"))
        return {"first": True, "dispatched": False}
    first = "first_presented_at" not in before
    if first:
        _put_event(event)
    elif not (before.get("dispatch_failed") and _claim_retry(ref)):
        return {"first": False, "dispatched": False}  # counted, nothing else to do
    incident = {"secret_ref": ref, "event_id": before.get("incident_event_id", event["event_id"])}
    try:
        dispatch.invoke(dispatch.burn_api_function(), {"canary_incident": incident}, wait=False)
    except Exception as exc:
        log.error("canary incident dispatch failed for %s: %s", ref, type(exc).__name__)
        _mark_dispatch_failed(ref)  # the next presentation retries
        return {"first": first, "dispatched": False}
    return {"first": first, "dispatched": True}
