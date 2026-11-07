"""Build ledger events that always match contracts/ledger-event.schema.json.

Every writer (gateway interceptors, proxy, Burn API, mock provider) calls
make_event(). It fills event_id and ts and rejects bad enum values early,
so a typo shows up as an exception in tests instead of a broken dashboard.
The enum sets below are checked against the JSON Schema by
tests/test_events.py, so they cannot drift.
"""
import re
from datetime import datetime, timezone

from .ids import new_event_id

SOURCES = {"tool_response", "egress", "handoff", "burn", "gateway", "session", "provider", "instance"}
ACTIONS = {
    "recorded", "blocked", "revoked", "rotated", "flagged",
    "refused", "issued", "inherited", "rejected",
    # instance lifecycle (source "instance"), and a badge Burn never issued (source "gateway")
    "started", "exited", "unregistered",
    # a session's canary was presented to the mock provider (source "provider")
    "canary_used",
}
SECRET_TYPES = {"aws_key", "api_key", "other"}
OWNERS = {"agent", "shared", "unknown"}
# Review severity of a flagged credential, lowest first. Levels only ever go up.
FLAG_LEVELS = ("seen", "attempted", "reported")

OPTIONAL_FIELDS = (
    "session_id", "tool", "secret_ref", "fingerprint", "secret_type", "owner",
    "owner_agent_id", "target_agent_id", "destination", "encoding", "detail",
    "role", "parent_session",
    "instance_id", "flag_level",
)

_SECRET_REF_RE = re.compile(r"^burn/(agent-[a-z0-9-]{1,40}|shared)/[a-z0-9_]{1,40}$")
_FP_RE = re.compile(r"^[0-9a-f]{64}$")
_AGENT_RE = re.compile(r"^(agent-[a-z0-9-]{1,40}|system)$")
_ROLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def utc_now_iso() -> str:
    """UTC timestamp with millisecond precision and a Z suffix."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def make_event(*, agent_id: str, source: str, action: str, **fields) -> dict:
    """Return a schema-valid event dict. Unknown keyword fields raise TypeError."""
    unknown = set(fields) - set(OPTIONAL_FIELDS)
    if unknown:
        raise TypeError(f"unknown event fields: {sorted(unknown)}")
    if not _AGENT_RE.match(agent_id or ""):
        raise ValueError(f"invalid agent_id: {agent_id!r}")
    if source not in SOURCES:
        raise ValueError(f"invalid source: {source!r}")
    if action not in ACTIONS:
        raise ValueError(f"invalid action: {action!r}")
    if fields.get("secret_type") is not None and fields["secret_type"] not in SECRET_TYPES:
        raise ValueError(f"invalid secret_type: {fields['secret_type']!r}")
    if fields.get("owner") is not None and fields["owner"] not in OWNERS:
        raise ValueError(f"invalid owner: {fields['owner']!r}")
    if fields.get("secret_ref") is not None and not _SECRET_REF_RE.match(fields["secret_ref"]):
        raise ValueError(f"invalid secret_ref: {fields['secret_ref']!r}")
    if fields.get("fingerprint") is not None and not _FP_RE.match(fields["fingerprint"]):
        raise ValueError("fingerprint must be 64 lowercase hex chars")
    if fields.get("role") is not None and not _ROLE_RE.match(fields["role"]):
        raise ValueError(f"invalid role: {fields['role']!r}")
    if fields.get("flag_level") is not None and fields["flag_level"] not in FLAG_LEVELS:
        raise ValueError(f"invalid flag_level: {fields['flag_level']!r}")
    if fields.get("detail") is not None and len(fields["detail"]) > 500:
        raise ValueError("detail longer than 500 chars")

    event = {
        "event_id": new_event_id(),
        "ts": utc_now_iso(),
        "agent_id": agent_id,
        "source": source,
        "action": action,
    }
    for name in OPTIONAL_FIELDS:
        event[name] = fields.get(name)
    return event
