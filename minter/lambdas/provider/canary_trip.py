"""Someone presented a canary to the provider: record it once per origin, maybe hand it on.

This is what makes detection independent of how the canary escaped (gzip,
base64, split across messages...): whoever ends up USING it has to send it here
in recognisable form, and its fingerprint row says which session it was planted in.

Where it came from decides what happens (contracts/README.md):
- "outside": no valid session proof. The only origin that may burn: the incident is
  dispatched to the Burn API, which applies the role's auto_burn and the trusted-signal policy.
- "own_session": the egress proxy vouches it is the canary's own session. Alert only.
- "burn_session": the proxy vouches for a DIFFERENT Burn session. Alert for review.

Bounded and idempotent, so replaying a copied canary cannot flood anything: one
conditional update on the canary's burn-credentials row counts every presentation,
and only the first presentation per origin writes a `canary_used` event. Only the
first outside presentation dispatches; a failed dispatch is retried by exactly one
later outside presentation. The provider never revokes anything itself.
"""
import logging

import boto3
from burn_contracts.events import for_storage, make_event, utc_now_iso
from burn_contracts.names import CREDENTIALS_TABLE, EVENTS_STREAM, EVENTS_TABLE, REGION, is_agent_id

import dispatch

log = logging.getLogger()
ORIGINS = ("outside", "own_session", "burn_session")


def _table(name: str):
    return boto3.resource("dynamodb", region_name=REGION).Table(name)


def _put_event(event: dict) -> None:
    _table(EVENTS_TABLE).put_item(Item={"stream": EVENTS_STREAM, **for_storage(event)})


def _count_presentation(secret_ref: str, origin: str, event_id: str) -> dict | None:
    """Bump the counters; the first outside presentation also pins the incident's event id.
    Returns the row as it was BEFORE this presentation, or None if the canary has no registry row."""
    sets = [f"first_{origin}_at = if_not_exists(first_{origin}_at, :now)", "last_presented_at = :now",
            "#s = :tripped"]
    values = {":now": utc_now_iso(), ":tripped": "tripped", ":one": 1, ":canary": "canary"}
    if origin == "outside":
        sets.append("incident_event_id = if_not_exists(incident_event_id, :evt)")
        values[":evt"] = event_id
    table = _table(CREDENTIALS_TABLE)
    try:
        resp = table.update_item(
            Key={"secret_ref": secret_ref},
            UpdateExpression=f"SET {', '.join(sets)} ADD presentations :one, presentations_{origin} :one",
            ConditionExpression="attribute_exists(secret_ref) AND secret_type = :canary",
            ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues=values,
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
        session_id=row.get("session_id"), secret_ref=row["secret_ref"], fingerprint=digest, secret_type="other",
        owner="agent" if is_agent_id(owner) else "unknown", owner_agent_id=owner if is_agent_id(owner) else None,
        detail=(detail + (f" (from {source_ip})" if source_ip else ""))[:500],
    )


def _detail(row: dict, origin: str, via_session: str | None) -> str:
    session = row.get("session_id")
    if origin == "own_session":
        return f"canary of session {session} used through its own session; alert only, not burned"
    if origin == "burn_session":
        return f"canary of session {session} used through another Burn session {via_session}; alert, review"
    return f"canary of session {session} presented at the provider from outside Burn; rejected"


def on_presented(row: dict, digest: str, source_ip: str | None = None, *,
                 origin: str = "outside", via_session: str | None = None) -> dict:
    """row: the canary's exact burn-fingerprints row. The caller always answers 401."""
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}")
    ref = row["secret_ref"]
    event = _event(row, digest, source_ip, _detail(row, origin, via_session))
    before = _count_presentation(ref, origin, event["event_id"])
    if before is None:
        # Fingerprint without a registry row (half-deleted reset): attribution is incomplete,
        # so record it for a human and never contain anything.
        _put_event(_event(row, digest, source_ip, "canary presented but it has no registry row; not contained"))
        return {"first": True, "dispatched": False, "origin": origin}
    first = f"first_{origin}_at" not in before
    if first:
        _put_event(event)
    if origin != "outside":
        return {"first": first, "dispatched": False, "origin": origin}  # alert only, never a burn
    if not first and not (before.get("dispatch_failed") and _claim_retry(ref)):
        return {"first": False, "dispatched": False, "origin": origin}  # counted, nothing else to do
    incident = {"secret_ref": ref, "event_id": before.get("incident_event_id", event["event_id"])}
    try:
        dispatch.invoke(dispatch.burn_api_function(), {"canary_incident": incident}, wait=False)
    except Exception as exc:
        log.error("canary incident dispatch failed for %s: %s", ref, type(exc).__name__)
        _mark_dispatch_failed(ref)  # the next outside presentation retries
        return {"first": first, "dispatched": False, "origin": origin}
    return {"first": first, "dispatched": True, "origin": origin}
