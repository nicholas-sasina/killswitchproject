"""POST /burn/by-source: burn everyone who read a source, and everyone they messaged afterwards.

A source is what a tool read, recorded by the Gateway on each ledger row as
source_ref = '<tool>:<resource>' (e.g. read_ticket:T-1001). When that ticket turns out
to be poisoned, or to have carried a secret, this finds:
  - every instance, running or exited, with a ledger row from that source since `since`;
  - every instance they messaged afterwards: send_message copies the sender's ledger rows,
    source_ref included, to the receiver (source "handoff"), at the time of the message,
    so the same index query returns them too, hop after hop.
Then it burns them all through the scoped-burn steps (burn_scopes): every active session
is revoked first, then each agent is burned like POST /burn within the API time budget.

The lookup is the burn-exposures index names.EXPOSURES_BY_SOURCE_INDEX (PK source_ref,
SK first_seen). A ledger row keeps the source of its first sighting; an instance that saw
the same credential earlier from somewhere else is listed under that earlier source.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key
from burn_contracts import names, tools

import burn_scopes
import db
import errors

MAX_REQUEST = 4096  # a bare resource (with `tool`) may be long; the recorded form never is
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _source_ref(body: dict) -> str:
    source_ref, tool = body.get("source_ref"), body.get("tool")
    if not isinstance(source_ref, str) or not source_ref or len(source_ref) > MAX_REQUEST:
        raise errors.ApiError(400, "source_ref is required ('<tool>:<resource>', or the resource with tool)")
    if tool is not None and (not isinstance(tool, str) or not re.fullmatch(r"[a-z_]{1,64}", tool)):
        raise errors.ApiError(400, "tool must be a tool name")
    # With tool and a bare resource, the resource is recorded the way the Gateway records it.
    return tools.requested_source_ref(source_ref, tool)


def _since(value) -> str | None:
    """The ledger's timestamp format (UTC, ms, Z), so the index range is a string comparison."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _TIMESTAMP.match(value):
        raise errors.ApiError(400, "since must be an ISO 8601 time")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise errors.ApiError(400, "since must be an ISO 8601 time")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def readers(source_ref: str, since: str | None) -> list[dict]:
    """One entry per agent: whether it read the source or was messaged by a reader, and when first."""
    condition = Key("source_ref").eq(source_ref)
    if since:
        condition = condition & Key("first_seen").gte(since)
    rows = db.query_all(db.table(names.EXPOSURES_TABLE), IndexName=names.EXPOSURES_BY_SOURCE_INDEX,
                        KeyConditionExpression=condition)
    found: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r.get("first_seen") or ""):
        via = "handoff" if row.get("source") == "handoff" else "read"
        entry = found.setdefault(row["agent_id"], {"agent_id": row["agent_id"], "via": via,
                                                   "first_seen": row.get("first_seen")})
        if via == "read":
            entry["via"] = "read"  # read it directly, even if a message also brought it
    return list(found.values())


def burn_by_source(body: dict) -> dict:
    source_ref, since = _source_ref(body), _since(body.get("since"))
    reason = str(body.get("reason") or f"read {source_ref}")[:200]
    found = readers(source_ref, since)
    if not found:
        raise errors.ApiError(404, "nobody read that source in the window")
    agent_ids = [r["agent_id"] for r in found]
    sessions = [s for agent_id in agent_ids for s in db.sessions_for_agent(agent_id)]  # running or exited
    deadline = time.monotonic() + burn_scopes.BURN_BUDGET_SECONDS
    pool = ThreadPoolExecutor(max_workers=burn_scopes.BURN_WORKERS)
    try:
        # The scoped-burn steps: revoke every active session first, then burn each agent once.
        revoked, _, revoke_failed = burn_scopes._revoke_all(pool, "by_source", source_ref, sessions, deadline)
        burned, failed = burn_scopes._burn_all(pool, agent_ids, reason, deadline, revoked, revoke_failed)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {"source_ref": source_ref, "since": since, "readers": found, "burned": burned, "failed": failed}
