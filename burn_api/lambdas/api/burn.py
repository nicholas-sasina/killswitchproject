"""POST /burn: contain one agent.

1. Revoke its sessions first. This is the part that must never fail.
2. Read what the agent saw (its ledger) and ask the minter to rotate its own
   credentials and flag the shared ones (level `seen`).
3. Raise credentials it tried to send out (blocked events) to `attempted`.
4. Record each outcome as an event for the dashboard.
5. For a burn of single instances, mark each for a clean replacement if its role asks (replace.py).

canary.py reuses the outcome and flag helpers below for single-session containment.
"""
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from burn_contracts import names

import db
import minter_client
import replace
import sessions

# Event sources that mean "a check stopped this credential from leaving".
BLOCKING_SOURCES = {"egress", "gateway", "handoff"}


def exposed_refs(agent_id: str) -> list[str]:
    """Credentials whose plaintext reached the agent. Unknown-format ones have no secret_ref, and
    an exposure with status "referenced" means it only ever got {{burn:ref:...}}: nothing to flag."""
    rows = db.query_all(
        db.table(names.EXPOSURES_TABLE), KeyConditionExpression=Key("agent_id").eq(agent_id)
    )
    return sorted({r["secret_ref"] for r in rows if r.get("secret_ref") and r.get("status") != "referenced"})


def _flag_event(agent_id: str, ref: str, level: str | None, **ids) -> None:
    owner = names.owner_of(ref)
    db.write_event(agent_id=agent_id, source="burn", action="flagged", secret_ref=ref,
                   owner="shared" if owner == names.SHARED_OWNER else "agent",
                   owner_agent_id=None if owner == names.SHARED_OWNER else owner, flag_level=level,
                   detail="agent saw this credential; it is not the agent's own, review it", **ids)


def record_outcomes(agent_id: str, result: dict, **ids) -> tuple[list[str], list[str]]:
    """Write rotated/flagged events for a minter rotate result. ids: e.g. session_id."""
    rotated, flagged = result.get("rotated", []), result.get("flagged", [])
    levels = result.get("flag_levels") or {}
    for ref in rotated:
        db.write_event(agent_id=agent_id, source="burn", action="rotated", secret_ref=ref,
                       owner="agent", owner_agent_id=agent_id,
                       detail="credential rotated; previous value no longer accepted", **ids)
    for ref in flagged:
        _flag_event(agent_id, ref, levels.get(ref), **ids)
    return rotated, flagged


def flag_seen(agent_id: str, refs: list[str], evidence: dict, **ids) -> list[str]:
    """Flag (level seen) what the agent saw but does not own, without rotating anything."""
    others = [r for r in refs if not names.is_canary_ref(r) and names.owner_of(r) != agent_id]
    if not others:
        return []
    try:
        out = minter_client.call("POST", "/flags", {
            "secret_refs": others, "level": "seen",
            "evidence": {"source": "burn", "agent_id": agent_id, **evidence}})
    except minter_client.MinterError as exc:
        print(f"flagging failed for {agent_id}: {exc}")
        return []
    for flag in out.get("flags", []):
        _flag_event(agent_id, flag["secret_ref"], flag["flag_level"], **ids)
    return [f["secret_ref"] for f in out.get("flags", [])]


def _blocked_events(agent_id: str) -> list[dict]:
    return db.query_all(
        db.table(names.EVENTS_TABLE), IndexName="by_agent",
        KeyConditionExpression=Key("agent_id").eq(agent_id), FilterExpression=Attr("action").eq("blocked"))


def _attempt_evidence(agent_id: str, event: dict) -> dict:
    evidence = {"source": event["source"], "event_id": event["event_id"], "agent_id": agent_id,
                "session_id": event.get("session_id"), "tool": event.get("tool"), "ts": event.get("ts")}
    return {k: v for k, v in evidence.items() if isinstance(v, str) and v}


def flag_blocked_attempts(agent_id: str) -> list[dict]:
    """Raise credentials the agent tried to send out (a check blocked them) to `attempted`.

    Best effort: flagging never fails a burn. Needs Query on the burn-events by_agent index.
    """
    try:
        events = sorted(_blocked_events(agent_id), key=lambda e: e["event_id"])
    except ClientError as exc:
        print(f"blocked events unavailable for {agent_id}: {exc.response['Error']['Code']}")
        return []
    first = {}
    for event in events:
        ref = event.get("secret_ref")
        if not ref or event.get("source") not in BLOCKING_SOURCES or names.is_canary_ref(ref):
            continue
        try:
            if names.owner_of(ref) == agent_id:
                continue  # its own credentials are rotated, not flagged
        except ValueError:
            continue
        first.setdefault(ref, event)  # the earliest attempt is the evidence
    raised = []
    for ref, event in first.items():
        try:
            out = minter_client.call("POST", "/flags", {"secret_refs": [ref], "level": "attempted",
                                                        "evidence": _attempt_evidence(agent_id, event)})
        except minter_client.MinterError as exc:
            print(f"attempted flag failed for {ref}: {exc}")
            continue
        for flag in out.get("flags", []):
            if flag["changed"]:
                _flag_event(agent_id, ref, flag["flag_level"], session_id=event.get("session_id"))
            raised.append(flag)
    return raised


def burn_agent(agent_id: str, reason: str = "", replaceable: list[str] | None = None,
               evidence_time: str | None = None) -> dict:
    """replaceable and evidence_time are for a burn of single instances (replace.py): the sessions the caller
    already revoked, and its earliest evidence of compromise. None for any other burn, which never replaces."""
    if db.get_agent(agent_id) is None:
        raise sessions.NotFound(agent_id)

    revoked_ids = sessions.revoke_sessions(agent_id)
    reason_text = f" ({reason[:200]})" if reason else ""
    db.write_event(
        agent_id=agent_id, source="burn", action="revoked",
        session_id=revoked_ids[-1] if revoked_ids else None,
        detail=f"{len(revoked_ids)} session(s) revoked{reason_text}",
    )

    result = minter_client.call(
        "POST", f"/agents/{agent_id}/rotate", {"exposed_refs": exposed_refs(agent_id)}
    )
    rotated, flagged = record_outcomes(agent_id, result)
    flag_blocked_attempts(agent_id)
    replace.after_burn(agent_id, replaceable, revoked_ids, evidence_time)  # best effort, never raises

    return {
        "agent_id": agent_id,
        # After a burn the agent has no usable badge, whether or not it had a session.
        "revoked": True,
        "sessions_revoked": revoked_ids,
        "rotated": rotated,
        "flagged": flagged,
    }
