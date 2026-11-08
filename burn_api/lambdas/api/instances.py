"""Instances: one per session. Listing, heartbeat, clean exit and the idle sweep.

An instance is a burn-sessions row. Role instances carry `role`; a scenario agent's session has
no `role` column and its role is derived from the agent id (agent-reader -> reader).

Idle sweep: an active role instance whose `last_seen` is older than BURN_INSTANCE_IDLE_SECONDS
(default 600) is exited like a clean exit. There is no scheduler; GET /instances runs the sweep
first, so the dashboard (which polls /instances) is what keeps it tidy. The sweep must not slow
the poll down: it stops after SWEEP_BUDGET_SECONDS or SWEEP_LIMIT instances, and a long-dead swarm
is cleaned up over a few calls. The status flip, badge revoke and event happen inline; deleting the
instance's credentials (a slow IAM call) is queued on the minter instead of awaited. Scenario
sessions have no `last_seen` and are never swept.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from burn_contracts import names
from burn_contracts.events import utc_now_iso

import db
import errors
import minter_client
import sessions

DEFAULT_IDLE_SECONDS = 600
SWEEP_LIMIT = 10
SWEEP_BUDGET_SECONDS = 2.0
MAX_LISTED = 500
COUNT_WORKERS = 16


def idle_seconds() -> int:
    try:
        return max(1, int(os.environ.get("BURN_INSTANCE_IDLE_SECONDS", DEFAULT_IDLE_SECONDS)))
    except ValueError:
        return DEFAULT_IDLE_SECONDS


def role_of(session: dict) -> str | None:
    if session.get("role"):
        return session["role"]
    try:
        return names.scenario_role_name(session["agent_id"])
    except ValueError:
        return None


def active_sessions() -> list[dict]:
    return db.scan_all(db.table(names.SESSIONS_TABLE), FilterExpression=Attr("status").eq("active"))


def _exposures(agent_id: str) -> int:
    return db.table(names.EXPOSURES_TABLE).query(  # credential rows only: read rows have no fingerprint
        KeyConditionExpression=Key("agent_id").eq(agent_id), FilterExpression=Attr("fingerprint").exists(),
        Select="COUNT")["Count"]


_pool: ThreadPoolExecutor | None = None


def _count_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=COUNT_WORKERS, thread_name_prefix="exposure-count")
    return _pool


def view(session: dict, exposures: int | None = None) -> dict:
    return {
        "session_id": session["session_id"], "agent_id": session["agent_id"], "role": role_of(session),
        "status": session["status"], "tools": list(session.get("tools", [])),
        **{k: session.get(k) for k in ("parent_session", "token_id", "owner", "created_by", "egress", "last_seen",
                                       "exited_at")},
        "created_at": session.get("created_at"),
        "exposures": _exposures(session["agent_id"]) if exposures is None else exposures,
    }


def get_session_or_none(session_id: str, consistent: bool = False) -> dict | None:
    return db.table(names.SESSIONS_TABLE).get_item(
        Key={"session_id": session_id}, ConsistentRead=consistent).get("Item")


def get_session(session_id: str, consistent: bool = False) -> dict:
    row = get_session_or_none(session_id, consistent)
    if row is None:
        raise errors.ApiError(404, "Unknown session")
    return row


def list_instances(role: str | None, status: str | None) -> list[dict]:
    if status is not None and status not in ("active", "revoked", "exited"):
        raise errors.ApiError(400, "status must be active, revoked or exited")
    if role is not None and not names.is_role_name(role):
        raise errors.ApiError(400, "bad role name")
    sweep_idle()
    # A filtered scan is fine at this scale; see the note on the lookups in db.py.
    rows = [s for s in db.scan_all(db.table(names.SESSIONS_TABLE)) if role_of(s)]
    rows = [s for s in rows if (role is None or role_of(s) == role) and (status is None or s["status"] == status)]
    rows.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    rows = rows[:MAX_LISTED]  # the dashboard shows the newest; older runs must not slow every poll
    # One COUNT query per row, run concurrently. The pool is kept between calls so each thread's
    # DynamoDB resource is built once per warm container, not on every poll.
    counts = list(_count_pool().map(lambda s: _exposures(s["agent_id"]), rows))
    return [view(s, n) for s, n in zip(rows, counts)]


def heartbeat(session_id: str) -> dict:
    get_session(session_id)  # 404 for an unknown session
    try:
        # Conditional, so a heartbeat can never mark a burned or exited instance as alive.
        row = db.table(names.SESSIONS_TABLE).update_item(
            Key={"session_id": session_id}, UpdateExpression="SET last_seen = :t",
            ConditionExpression="#s = :active", ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":t": utc_now_iso(), ":active": "active"}, ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        current = get_session(session_id)["status"]
        raise errors.ApiError(409, f"Instance is {current}") from None
    return view(row)


def _delete_credentials(session: dict, wait: bool):
    # Only a role instance owns its credentials. A scenario agent keeps them for its next session.
    if session.get("role"):
        send = minter_client.call if wait else minter_client.call_async
        send("DELETE", f"/agents/{session['agent_id']}/credentials")


def exit_instance(session_id: str, wait_for_credentials: bool = True, idle_before: str | None = None) -> dict:
    """Clean exit: end the session, then delete the instance's own credentials.

    The badge goes first (it is the kill switch). If the minter then fails, the error is raised
    after the session is already exited; calling exit again retries the credential deletion,
    which is idempotent. With wait_for_credentials=False the deletion is queued, not awaited.
    With idle_before (the sweep), the instance exits only if it was still unseen since then.
    """
    session = get_session(session_id)
    if session["status"] == "active" and sessions.end_session(session, "exited", idle_before):
        session = get_session(session_id)
        db.write_event(agent_id=session["agent_id"], source="instance", action="exited",
                       session_id=session_id, role=role_of(session),
                       parent_session=session.get("parent_session"), detail="instance exited")
        _delete_credentials(session, wait_for_credentials)
    elif session["status"] == "exited":
        _delete_credentials(session, wait_for_credentials)
    return view(get_session(session_id))


def sweep_idle(limit: int = SWEEP_LIMIT, budget: float = SWEEP_BUDGET_SECONDS) -> list[str]:
    """Exit active role instances that have not been seen for BURN_INSTANCE_IDLE_SECONDS."""
    deadline = time.monotonic() + budget
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=idle_seconds()))
    stale = db.scan_all(db.table(names.SESSIONS_TABLE), FilterExpression=(
        Attr("status").eq("active") & Attr("role").exists() & Attr("last_seen").lt(cutoff)))
    exited = []
    for session in sorted(stale, key=lambda s: s["last_seen"])[:limit]:
        if time.monotonic() >= deadline:
            break
        try:
            result = exit_instance(session["session_id"], wait_for_credentials=False, idle_before=cutoff)
            if result["status"] == "exited":  # a heartbeat that landed first keeps it alive
                exited.append(session["session_id"])
        except Exception as exc:  # one stuck instance must not stop the listing or the sweep
            print(f"idle sweep: exit failed for {session['session_id']}: {type(exc).__name__}")
    return exited


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
