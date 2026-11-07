"""Canary incident: contain exactly the session whose canary was used.

The mock provider hands us {"secret_ref", "event_id"} by direct async invoke.
Nothing in that payload is trusted: attribution is re-read from burn-credentials.

Trusted-signal policy (contracts/README.md). Contain automatically only if ALL hold:
  1. the ref is a registered canary that maps to exactly one agent + session;
  2. that agent's exposure ledger has the canary, and no other agent's does
     (it was really delivered to that session, and only there);
  3. the session row exists, belongs to the same agent (and instance), and is active.
Otherwise the incident is recorded and flagged for a human. Nothing broader than
the one session is ever revoked, so a copied canary replayed on purpose can at
worst contain the single session that canary was planted in.

Containment:
  a. Revoke the session with a conditional update (active -> revoked). Only one
     caller can win it, so concurrent or repeated incidents contain once.
  b. Delete its badge client (the gateway already refuses it after step a).
  c. Rotate the agent's own credentials, but only if no other active session
     uses them. Otherwise rotation is deferred and reported; never widened.
  d. Flag the shared credentials it saw (`seen`, or higher from blocked attempts).
A failed rotation leaves the session revoked and reports containment incomplete.
"""
import time
from datetime import datetime, timezone

import badges  # bundled from the identity code at deploy time
from boto3.dynamodb.conditions import Key
from burn_contracts import names

import burn
import db
import minter_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _canary_row(secret_ref: str) -> dict | None:
    row = db.table(names.CREDENTIALS_TABLE).get_item(
        Key={"secret_ref": secret_ref}, ConsistentRead=True).get("Item")
    return row if row and row.get("secret_type") == "canary" else None


def _ledger_row(agent_id: str, secret_ref: str) -> dict | None:
    items = db.table(names.EXPOSURES_TABLE).query(
        KeyConditionExpression=Key("agent_id").eq(agent_id) & Key("secret_ref_or_fp").eq(secret_ref),
        ConsistentRead=True)["Items"]
    return items[0] if items else None


def _other_holders(agent_id: str, secret_ref: str) -> list[str]:
    """Other scenario agents whose ledger has this canary (e.g. inherited via send_message)."""
    meta = db.table(names.SCENARIO_TABLE).get_item(Key={"pk": "meta"}).get("Item")
    agent_ids = meta["data"].get("agent_ids", []) if meta else []
    return [a for a in agent_ids if a != agent_id and _ledger_row(a, secret_ref)]


def _revoke_if_active(session_id: str, event_id: str | None) -> bool:
    """The one atomic step: True only for the caller that moved the session active -> revoked."""
    tbl = db.table(names.SESSIONS_TABLE)
    try:
        tbl.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET #s = :revoked, revoked_at = :t, revoked_by = :why",
            ConditionExpression="#s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":revoked": "revoked", ":active": "active", ":t": _now(),
                                       ":why": f"canary:{event_id or 'unknown'}"},
        )
        return True
    except tbl.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def _not_contained(ref: str, agent_id: str, session_id, instance_id, reason: str) -> dict:
    db.write_event(
        agent_id=agent_id if names.is_agent_id(agent_id or "") else "system", source="burn", action="flagged",
        session_id=session_id, instance_id=instance_id, secret_ref=ref, secret_type="other",
        detail=f"canary incident not contained: {reason}; review",
    )
    return {"outcome": "not_actionable", "reason": reason, "agent_id": agent_id, "session_id": session_id,
            "instance_id": instance_id, "rotated": [], "flagged": []}


def _check_attribution(ref: str, row: dict | None):
    """Return (agent_id, session, instance_id, ledger_row) or a reason string."""
    if row is None:
        return "not a registered canary"
    agent_id, session_id, instance_id = row.get("owner_agent_id"), row.get("session_id"), row.get("instance_id")
    try:
        if names.canary_secret_ref(agent_id, session_id) != ref:
            return "canary row does not match its session"
    except (ValueError, TypeError):
        return "canary has no usable agent/session attribution"
    exposure = _ledger_row(agent_id, ref)
    if exposure is None:
        return "no exposure evidence: the canary was never delivered to this agent"
    others = _other_holders(agent_id, ref)
    if others:
        return f"ambiguous: the canary also reached {', '.join(sorted(others)[:5])}"
    session = db.table(names.SESSIONS_TABLE).get_item(
        Key={"session_id": session_id}, ConsistentRead=True).get("Item")
    if session is None:
        return "session unknown"
    if session.get("agent_id") != agent_id:
        return "session belongs to a different agent"
    if instance_id and session.get("instance_id") not in (None, instance_id):
        return "session belongs to a different instance"
    return agent_id, session, instance_id, exposure


def handle_incident(incident: dict) -> dict:
    start = time.perf_counter()
    ref = incident.get("secret_ref") if isinstance(incident, dict) else None
    event_id = incident.get("event_id") if isinstance(incident, dict) else None
    if not isinstance(ref, str) or not names.is_canary_ref(ref):
        return {"outcome": "ignored", "reason": "not a canary reference"}
    row = _canary_row(ref)
    checked = _check_attribution(ref, row)
    if isinstance(checked, str):
        row = row or {}
        return _not_contained(ref, row.get("owner_agent_id"), row.get("session_id"), row.get("instance_id"),
                              checked)
    agent_id, session, instance_id, exposure = checked
    session_id = session["session_id"]

    if not _revoke_if_active(session_id, event_id):
        return {"outcome": "already_contained", "reason": "session is no longer active", "agent_id": agent_id,
                "session_id": session_id, "instance_id": instance_id, "rotated": [], "flagged": []}
    revoked_ms = _ms(start)
    try:
        badges.delete_badge(session["client_id"])
    except Exception as exc:  # already revoked in DynamoDB; a leftover client is harmless
        print(f"delete_badge failed for {session_id}: {type(exc).__name__}")
    db.write_event(
        agent_id=agent_id, source="burn", action="revoked", session_id=session_id, instance_id=instance_id,
        secret_ref=ref, detail=(f"canary incident {event_id}: session revoked; canary first read via "
                                f"{exposure.get('via') or 'unknown tool'} at {exposure.get('first_seen') or '?'}")[:500],
    )

    evidence = {k: v for k, v in {"event_id": event_id, "session_id": session_id,
                                  "instance_id": instance_id}.items() if v}
    exposed = burn.exposed_refs(agent_id)
    sharing = [s["session_id"] for s in db.sessions_for_agent(agent_id)
               if s.get("status") == "active" and s["session_id"] != session_id]
    outcome, rotated, flagged = "contained", [], []
    if sharing:
        # The agent's credentials are also in use by other live sessions: rotating them would
        # break those. Revoke only, report the gap, flag what it saw.
        outcome = "contained_rotation_deferred"
        db.write_event(agent_id=agent_id, source="burn", action="flagged", session_id=session_id,
                       instance_id=instance_id,
                       detail=f"rotation deferred: {len(sharing)} other active session(s) use the same "
                              f"credentials of {agent_id}; needs instance-scoped credentials")
        flagged = burn.flag_seen(agent_id, exposed, evidence, session_id=session_id, instance_id=instance_id)
    else:
        try:
            result = minter_client.call("POST", f"/agents/{agent_id}/rotate",
                                        {"exposed_refs": exposed, "evidence": evidence})
            rotated, flagged = burn.record_outcomes(agent_id, result, session_id=session_id,
                                                    instance_id=instance_id)
        except minter_client.MinterError:
            outcome = "contained_rotation_failed"
            db.write_event(agent_id=agent_id, source="burn", action="flagged", session_id=session_id,
                           instance_id=instance_id,
                           detail="rotation failed: session stays revoked, credentials NOT rotated; "
                                  "containment incomplete, burn the agent manually")
    rotated_ms = _ms(start)
    burn.flag_blocked_attempts(agent_id)
    return {"outcome": outcome, "agent_id": agent_id, "session_id": session_id, "instance_id": instance_id,
            "rotated": rotated, "flagged": flagged, "rotation_deferred_for": sharing,
            "timings_ms": {"revoked": revoked_ms, "rotation_done": rotated_ms}}
