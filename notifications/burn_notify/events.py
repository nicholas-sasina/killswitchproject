"""Turn whatever delivered a BURN ledger event into one validated event dict.

Accepted envelopes (all carry the same ledger row from the burn-events table):
  * a DynamoDB Streams record ({"eventName": "INSERT", "dynamodb": {"NewImage": {...}}})
  * an SQS message whose body is such a stream record (EventBridge Pipes -> SQS)
  * a plain ledger event dict (tests, dry runs)

Only INSERTs matter: the ledger is append-only, so MODIFY/REMOVE records are ignored
and never produce a notification.

The returned dict holds only the fields named in contracts/ledger-event.schema.json;
anything else on the row (e.g. the "stream" partition key) is dropped. Values are
checked against the contract's enums and patterns, so a malformed or hostile row is
rejected here instead of reaching a formatter. tests/test_schema_compat.py keeps these
sets equal to the contract's.
"""
from __future__ import annotations

import json
import re
from typing import Any

SOURCES = frozenset({"tool_response", "egress", "handoff", "burn", "gateway", "session", "provider", "instance"})
ACTIONS = frozenset({
    "recorded", "blocked", "revoked", "rotated", "flagged", "refused", "issued", "inherited", "rejected",
    "started", "exited", "unregistered", "canary_used", "dismissed", "replaced", "resumed",
})
FLAG_LEVELS = ("seen", "attempted", "reported")
OWNERS = frozenset({"agent", "shared", "unknown"})

REQUIRED = ("event_id", "ts", "agent_id", "source", "action")
OPTIONAL = (
    "session_id", "tool", "secret_ref", "fingerprint", "secret_type", "owner",
    "owner_agent_id", "target_agent_id", "destination", "encoding", "detail",
    "role", "parent_session", "flag_level", "actor", "replaces", "replaced_by",
)

EVENT_ID_RE = re.compile(r"^evt_[0-9A-HJKMNP-TV-Z]{26}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$")
AGENT_RE = re.compile(r"^(agent-[a-z0-9-]{1,40}|system)$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
ROLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SECRET_REF_RE = re.compile(r"^burn/(agent-[a-z0-9-]{1,40}|shared)/[a-z0-9_]{1,40}$")
CANARY_PREFIX = "canary_"


class InvalidEvent(ValueError):
    """The record is not a usable ledger event. Its message never echoes field values."""


def is_canary_ref(secret_ref: str | None) -> bool:
    parts = (secret_ref or "").split("/")
    return len(parts) == 3 and parts[0] == "burn" and parts[2].startswith(CANARY_PREFIX)


def _deserialize(image: dict) -> dict:
    """DynamoDB-JSON NewImage -> plain dict. Ledger rows only hold strings and NULLs."""
    out: dict[str, Any] = {}
    for key, typed in image.items():
        if not isinstance(typed, dict) or len(typed) != 1:
            raise InvalidEvent(f"unexpected attribute shape for {key!r}")
        (kind, value), = typed.items()
        if kind == "S":
            out[key] = value
        elif kind == "NULL":
            out[key] = None
        elif kind == "N":
            out[key] = value  # never used by ledger fields; kept as text, then dropped
        else:
            out[key] = None  # B/SS/M/L etc. are not part of the contract; ignored
    return out


def unwrap(record: Any) -> dict | None:
    """Return the raw ledger row inside an envelope, or None for records to ignore."""
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except ValueError as exc:
            raise InvalidEvent("body is not JSON") from exc
    if not isinstance(record, dict):
        raise InvalidEvent("record is not an object")
    if "body" in record and ("receiptHandle" in record or "messageId" in record):  # SQS message
        return unwrap(record["body"])
    if "dynamodb" in record:  # DynamoDB Streams record (directly or as forwarded by Pipes)
        if record.get("eventName") != "INSERT":
            return None
        image = (record.get("dynamodb") or {}).get("NewImage")
        if not isinstance(image, dict):
            raise InvalidEvent("stream record has no NewImage (stream view type must include new images)")
        return _deserialize(image)
    return record


def validate(row: dict) -> dict:
    """Return a contract-shaped copy of row or raise InvalidEvent."""
    for name in REQUIRED:
        if not isinstance(row.get(name), str):
            raise InvalidEvent(f"missing required field {name}")
    event = {name: row[name] for name in REQUIRED}
    for name in OPTIONAL:
        value = row.get(name)
        event[name] = value if isinstance(value, str) else None
    if not EVENT_ID_RE.match(event["event_id"]):
        raise InvalidEvent("bad event_id")
    if not TS_RE.match(event["ts"]):
        raise InvalidEvent("bad ts")
    if not AGENT_RE.match(event["agent_id"]):
        raise InvalidEvent("bad agent_id")
    if event["source"] not in SOURCES:
        raise InvalidEvent("unknown source")
    if event["action"] not in ACTIONS:
        raise InvalidEvent("unknown action")
    if event["flag_level"] is not None and event["flag_level"] not in FLAG_LEVELS:
        raise InvalidEvent("bad flag_level")
    if event["owner"] is not None and event["owner"] not in OWNERS:
        raise InvalidEvent("bad owner")
    if event["secret_ref"] is not None and not SECRET_REF_RE.match(event["secret_ref"]):
        raise InvalidEvent("bad secret_ref")
    # Identifiers that may be shown in a message must be plain identifiers, not free text.
    for name in ("session_id", "parent_session", "replaces", "replaced_by"):
        if event[name] is not None and not SESSION_RE.match(event[name]):
            event[name] = None
    if event["role"] is not None and not ROLE_RE.match(event["role"]):
        event["role"] = None
    if event["detail"] is not None:
        event["detail"] = event["detail"][:500]
    return event


TEST_ENVELOPE_KEY = "burn_notify_test"


def parse(record: Any) -> dict | None:
    """unwrap + validate. None means "not an insert, nothing to do"."""
    row = unwrap(record)
    if isinstance(row, dict) and TEST_ENVELOPE_KEY in row:
        row = row.get("event")
        if not isinstance(row, dict):
            raise InvalidEvent("test envelope has no event")
    return None if row is None else validate(row)


def is_test_envelope(record: Any) -> bool:
    """A deployment check message (see `burn_notify test-message`): always delivered labelled [TEST]."""
    try:
        row = unwrap(record)
    except InvalidEvent:
        return False
    return isinstance(row, dict) and row.get(TEST_ENVELOPE_KEY) is True


_CROCKFORD = {c: i for i, c in enumerate("0123456789ABCDEFGHJKMNPQRSTVWXYZ")}


def event_time_ms(event_id: str) -> int | None:
    """Milliseconds encoded in an evt_<ULID> id (its first 10 characters), or None."""
    if not isinstance(event_id, str) or not EVENT_ID_RE.match(event_id):
        return None
    value = 0
    for char in event_id[4:14]:
        value = value * 32 + _CROCKFORD[char]
    return value
