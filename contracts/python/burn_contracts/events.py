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
from .names import SESSION_ID_RE

SOURCES = {"tool_response", "egress", "handoff", "burn", "gateway", "session", "provider", "instance"}
ACTIONS = {
    "recorded", "blocked", "revoked", "rotated", "flagged",
    "refused", "issued", "inherited", "rejected",
    # instance lifecycle (source "instance"), and a badge Burn never issued (source "gateway")
    "started", "exited", "unregistered",
    # a session's canary was presented to the mock provider (source "provider"),
    # and an owner dismissed a flagged credential after review (source "burn")
    "canary_used", "dismissed",
    # replace on burn (source "instance"): a burned instance was replaced, and its replacement resumed the task
    "replaced", "resumed",
}
SECRET_TYPES = {"aws_key", "api_key", "other"}
OWNERS = {"agent", "shared", "unknown"}
# Review severity of a flagged credential, lowest first. Levels only ever go up.
FLAG_LEVELS = ("seen", "attempted", "reported")

OPTIONAL_FIELDS = (
    "session_id", "tool", "secret_ref", "fingerprint", "secret_type", "owner",
    "owner_agent_id", "target_agent_id", "destination", "encoding", "detail",
    "role", "parent_session",
    "flag_level", "actor",
    "replaces", "replaced_by",
)

_SECRET_REF_RE = re.compile(r"^burn/(agent-[a-z0-9-]{1,40}|shared)/[a-z0-9_]{1,40}$")
_FP_RE = re.compile(r"^[0-9a-f]{64}$")
_AGENT_RE = re.compile(r"^(agent-[a-z0-9-]{1,40}|system)$")
_ACTOR_RE = re.compile(r"^(system|admin|instance:[A-Za-z0-9_.:-]{1,128}|[^@\s]{1,64}@[^@\s]{1,255})$")
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
    if fields.get("actor") is not None and not _ACTOR_RE.match(fields["actor"]):
        raise ValueError(f"invalid actor: {fields['actor']!r}")
    if fields.get("flag_level") is not None and fields["flag_level"] not in FLAG_LEVELS:
        raise ValueError(f"invalid flag_level: {fields['flag_level']!r}")
    for link in ("replaces", "replaced_by"):
        if fields.get(link) is not None and not SESSION_ID_RE.fullmatch(fields[link]):
            raise ValueError(f"invalid {link}: {fields[link]!r}")
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


# --- storage representation -------------------------------------------------
# make_event() returns every optional field, set to None where unused, because
# in-process callers and the JSON Schema both expect the full shape. A typical
# event uses four or five of the seventeen, so writing them all put roughly
# 190 bytes of attribute names and NULLs into every row: over half of a
# measured 325-byte average.
#
# DynamoDB rows therefore carry only the fields that have a value, plus a TTL.
# for_storage() and from_storage() are inverses, so what the API returns is
# unchanged; only what DynamoDB holds and what a query has to read gets
# smaller. The schema is untouched, which matters because it sets
# additionalProperties: false and a TTL attribute on the event itself would
# fail validation.

#: Numeric epoch-seconds attribute DynamoDB uses to expire a row. Distinct
#: from the ISO `expires_at` on enroll tokens, which is an application check.
TTL_ATTRIBUTE = "ttl_epoch"

#: How long a ledger event is kept. Long enough that no demo, eval run or
#: post-mortem loses history; short enough that the table does not grow
#: without bound. Rows written before TTL was enabled have no TTL attribute
#: and are never expired, so enabling this cannot delete existing history.
EVENT_RETENTION_DAYS = 30


def ttl_epoch(days: int, now: float | None = None) -> int:
    """Epoch seconds `days` from now, for a DynamoDB TTL attribute."""
    import time

    return int((time.time() if now is None else now) + days * 86400)


def for_storage(event: dict, retention_days: int = EVENT_RETENTION_DAYS) -> dict:
    """The row to write: no None values, plus a TTL so the table self-trims."""
    row = {key: value for key, value in event.items() if value is not None}
    row[TTL_ATTRIBUTE] = ttl_epoch(retention_days)
    return row


def from_storage(row: dict) -> dict:
    """The event as callers expect it: every optional field present, TTL gone."""
    event = {key: value for key, value in row.items() if key != TTL_ATTRIBUTE}
    for name in OPTIONAL_FIELDS:
        event.setdefault(name, None)
    return event
