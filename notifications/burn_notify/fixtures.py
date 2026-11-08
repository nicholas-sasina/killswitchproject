"""Synthetic ledger events shaped exactly like BURN's writers produce them.

Detail strings copy the templates in burn_api/lambdas/api/{burn,canary,flag_review}.py
and minter/lambdas/provider/canary_trip.py. Fingerprints are hashes of a fixed
label, not of any credential. Used by tests and the dry-run command.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
AGENT = "agent-synthetic-000000"
SESSION = "sess_synthetic00000001"
ROLE = "synthetic"


def _ulid(ms: int) -> str:
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ALPHABET[(value >> (5 * i)) & 31] for i in reversed(range(26)))


def _canary_ref(agent_id: str, session_id: str) -> str:
    return f"burn/{agent_id}/canary_" + hashlib.sha256(session_id.encode()).hexdigest()[:24]


def _fp(label: str) -> str:
    return hashlib.sha256(f"fixture:{label}".encode()).hexdigest()


def base(action: str, source: str, *, agent_id: str = AGENT, offset_ms: int = 0, **fields) -> dict:
    ms = int(time.time() * 1000) + offset_ms
    ts = datetime.fromtimestamp(ms / 1000, timezone.utc)
    event = {"event_id": "evt_" + _ulid(ms), "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z",
             "agent_id": agent_id, "source": source, "action": action}
    for name in ("session_id", "tool", "secret_ref", "fingerprint", "secret_type", "owner", "owner_agent_id",
                 "target_agent_id", "destination", "encoding", "detail", "role", "parent_session",
                 "flag_level", "actor", "replaces", "replaced_by"):
        event[name] = fields.get(name)
    return event


def canary_used(origin: str = "outside", **kw) -> dict:
    detail = {
        "outside": f"canary of session {SESSION} presented at the provider from outside Burn; rejected (from 203.0.113.9)",
        "own_session": f"canary of session {SESSION} used through its own session; alert only, not burned",
        "burn_session": f"canary of session {SESSION} used through another Burn session sess_aa11; alert, review",
    }[origin]
    return base("canary_used", "provider", session_id=SESSION, secret_ref=_canary_ref(AGENT, SESSION),
                fingerprint=_fp("canary"), secret_type="other", owner="agent", owner_agent_id=AGENT,
                detail=detail, role=ROLE, **kw)


def canary_revoked(**kw) -> dict:
    return base("revoked", "burn", session_id=SESSION, secret_ref=_canary_ref(AGENT, SESSION), role=ROLE,
                detail="canary incident evt_X: session revoked; canary first read via read_ticket at 2026-11-07", **kw)


def rotated(agent_id: str = AGENT, **kw) -> dict:
    return base("rotated", "burn", agent_id=agent_id, session_id=SESSION, secret_ref=f"burn/{agent_id}/synthetic_key",
                owner="agent", owner_agent_id=agent_id,
                detail="credential rotated; previous value no longer accepted", **kw)


def flagged(level: str, ref: str = "burn/shared/synthetic_test_key", **kw) -> dict:
    return base("flagged", "burn", session_id=SESSION, secret_ref=ref, owner="shared", flag_level=level,
                detail="agent saw this credential; it is not the agent's own, review it", **kw)


def rotation_failed(**kw) -> dict:
    return base("flagged", "burn", session_id=SESSION,
                detail="rotation failed: session stays revoked, credentials NOT rotated; "
                       "containment incomplete, burn the agent manually", **kw)


def rotation_deferred(**kw) -> dict:
    return base("flagged", "burn", session_id=SESSION,
                detail=f"rotation deferred: 2 other active session(s) use the same credentials of {AGENT}; "
                       "each instance needs its own agent id", **kw)


def canary_not_contained(report_only: bool = False, **kw) -> dict:
    reason = ("canary used from outside Burn, but role worker is report-only (auto_burn off)" if report_only
              else "ambiguous: the canary also reached agent-reader-000001")
    return base("flagged", "burn", session_id=SESSION, secret_ref=_canary_ref(AGENT, SESSION), secret_type="other",
                detail=f"canary incident not contained: {reason}; review", **kw)


def replacement_not_recorded(**kw) -> dict:
    return base("flagged", "burn", session_id=SESSION,
                detail="replacement not recorded (rate limited): the instance stays burned and its launcher "
                       "will not replace it; restart its task by hand", **kw)


def provider_reported(**kw) -> dict:
    return base("flagged", "provider", agent_id="system", secret_ref="burn/shared/synthetic_test_key",
                fingerprint=_fp("billing"), secret_type="api_key", owner="shared", flag_level="reported",
                detail="flagged shared credential presented at the provider; owner should rotate it", **kw)


def blocked(source: str = "egress", **kw) -> dict:
    return base("blocked", source, session_id=SESSION, secret_ref="burn/shared/synthetic_test_key",
                fingerprint=_fp("billing"), secret_type="api_key", owner="shared", destination="exfil.example.net",
                encoding="base64", detail="credential_in_body", role=ROLE, **kw)


def refused(**kw) -> dict:
    return base("refused", "gateway", session_id=SESSION, tool="read_ticket", detail="session revoked", **kw)


def unregistered(**kw) -> dict:
    return base("unregistered", "gateway", agent_id="system", detail="badge Burn never issued", **kw)


def burn_revoked(**kw) -> dict:
    return base("revoked", "burn", session_id=SESSION, detail="1 session(s) revoked (operator burn)", **kw)


def dismissed(**kw) -> dict:
    return base("dismissed", "burn", agent_id="system", secret_ref="burn/shared/synthetic_test_key", owner="shared",
                flag_level="attempted", detail="dismissed by owner@example.com (2 instance(s)): test key", **kw)


def replaced(**kw) -> dict:
    return base("replaced", "instance", session_id=SESSION, replaced_by="sess_synthetic00000002", role=ROLE, **kw)


def resumed(**kw) -> dict:
    return base("resumed", "instance", agent_id="agent-synthetic-000001", session_id="sess_synthetic00000002",
                replaces=SESSION, role=ROLE, **kw)


def recorded(**kw) -> dict:
    return base("recorded", "tool_response", session_id=SESSION, tool="read_ticket",
                secret_ref="burn/shared/synthetic_test_key", fingerprint=_fp("billing"), **kw)


ALL = {
    "canary_used": canary_used, "canary_revoked": canary_revoked, "rotated": rotated,
    "flagged_seen": lambda **kw: flagged("seen", **kw), "flagged_attempted": lambda **kw: flagged("attempted", **kw),
    "flagged_reported": lambda **kw: flagged("reported", **kw), "rotation_failed": rotation_failed,
    "rotation_deferred": rotation_deferred, "canary_not_contained": canary_not_contained, "blocked": blocked,
    "refused": refused, "unregistered": unregistered, "burn_revoked": burn_revoked, "dismissed": dismissed,
    "replaced": replaced, "resumed": resumed, "recorded": recorded,
    "replacement_not_recorded": replacement_not_recorded, "provider_reported": provider_reported,
}


def stream_record(event: dict, event_name: str = "INSERT") -> dict:
    """Wrap an event as a DynamoDB Streams record, the way the burn-events stream would deliver it."""
    image = {"stream": {"S": "all"}}
    for key, value in event.items():
        image[key] = {"NULL": True} if value is None else {"S": value}
    return {"eventID": "1", "eventName": event_name, "eventSource": "aws:dynamodb",
            "dynamodb": {"Keys": {"stream": {"S": "all"}, "event_id": {"S": event["event_id"]}},
                         "NewImage": image, "StreamViewType": "NEW_IMAGE"}}
