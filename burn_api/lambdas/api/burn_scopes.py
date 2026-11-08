"""POST /burns: burn a set of instances at once.

1. Resolve the target sessions for the scope (instance, role, lineage, token).
2. Revoke every active target session first. This is what stops them, and it happens before any
   slow work, so a timeout later cannot leave a target running.
3. Burn each distinct agent id exactly like POST /burn (rotate its credentials, flag shared ones
   it saw) in a small thread pool. Failures are collected in `failed`, never raised.

Time: the API has a 29 s limit. Each burn is a few DynamoDB calls plus one minter round trip
(IAM key rotation), so 20 instances run in ceil(20 / BURN_WORKERS) waves of the minter latency.
Burning stops waiting after BURN_BUDGET_SECONDS and reports whatever has not finished in `failed`
(those instances are already revoked; finish them with POST /burn {agent_id}).
"""
import contextvars
import time
from concurrent.futures import ThreadPoolExecutor, wait

from burn_contracts import names

import burn
import db
import errors
import instances
import sessions
import tokens

SCOPES = ("instance", "role", "lineage", "token")
BURN_WORKERS = 10
BURN_BUDGET_SECONDS = 24


def _submit(pool, fn, *args):
    """Run fn in the pool with this request's context, so its events name the same caller (caller.py).
    One copy per task: a context cannot be entered by two threads at once."""
    return pool.submit(contextvars.copy_context().run, fn, *args)


def _lineage(root_id: str) -> list[dict]:
    found, seen, frontier = [instances.get_session(root_id, consistent=True)], {root_id}, [root_id]
    while True:  # ends when a level has no new children; `seen` also breaks any cycle
        children = [s for s in db.sessions_for_parents(frontier) if s["session_id"] not in seen]
        if not children:
            return found
        found += children
        seen.update(s["session_id"] for s in children)
        frontier = [s["session_id"] for s in children]


def _role_sessions(role: str) -> list[dict]:
    import roles
    roles.lookup(role)  # 404 for an unknown role
    found = db.sessions_for_role(role)
    if db.get_scenario_agent("agent-" + role):  # a scenario session has no role column; find it by agent id
        found += db.sessions_for_agent("agent-" + role)
    return found


def targets(scope: str, ident: str) -> list[dict]:
    if scope == "instance":
        return [instances.get_session(ident)]
    if scope == "role":
        return _role_sessions(ident)
    if scope == "lineage":
        return _lineage(ident)
    if scope == "token":
        tokens.revoke(ident)  # 404 for an unknown token; also stops further joins
        return db.sessions_for_token(ident)
    raise errors.ApiError(400, f"scope must be one of {', '.join(SCOPES)}")


def burn_scope(scope: str, ident: str, reason: str = "", budget: float = BURN_BUDGET_SECONDS) -> dict:
    if not isinstance(ident, str) or not ident:
        raise errors.ApiError(400, "id is required")
    deadline = time.monotonic() + budget
    found = targets(scope, ident)

    pool = ThreadPoolExecutor(max_workers=BURN_WORKERS)
    try:
        revoked, agent_ids, revoke_failed = _revoke_all(pool, scope, ident, found, deadline)
        burned, failed = _burn_all(pool, agent_ids, reason, deadline, revoked, revoke_failed)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {"scope": scope, "id": ident, "burned": burned, "failed": failed}


def _revoke_all(pool, scope, ident, found, deadline):
    """Revoke every active target session, in the pool, before anything slow runs.

    A lineage is scanned again after each pass: a child that was being spawned during the first
    scan either shows up now or sees its revoked parent when it re-checks (sessions.py), so it
    cannot escape. Returns (session ids revoked per agent, every agent id, agents whose revoke
    did not finish).
    """
    revoked: dict[str, list[str]] = {}
    agent_ids: dict[str, None] = {}
    revoke_failed: set[str] = set()
    handled: set[str] = set()
    while True:
        fresh = [s for s in found if s["status"] == "active" and s["session_id"] not in handled]
        if not fresh:
            break
        handled.update(s["session_id"] for s in fresh)
        futures = {_submit(pool, sessions.end_session, s, "revoked"): s for s in fresh}
        done, _ = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        for future, session in futures.items():
            agent_ids.setdefault(session["agent_id"])
            try:
                if future in done and future.result():
                    revoked.setdefault(session["agent_id"], []).append(session["session_id"])
                elif future not in done:
                    revoke_failed.add(session["agent_id"])
            except Exception:
                # The burn below revokes this agent's sessions again; it is only reported if that fails too.
                revoke_failed.add(session["agent_id"])
        if scope != "lineage" or time.monotonic() >= deadline:
            break
        found = targets(scope, ident)
    return revoked, list(agent_ids), revoke_failed


def _burn_all(pool, agent_ids, reason, deadline, revoked, revoke_failed):
    """Burn each distinct agent once (exactly like POST /burn). Failures are collected, not raised."""
    burned, failed = [], []
    futures = {_submit(pool, burn.burn_agent, agent_id, reason): agent_id for agent_id in agent_ids}
    _, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    for future, agent_id in futures.items():
        if future in pending:
            failed.append({"agent_id": agent_id, "error": _describe(None, agent_id in revoke_failed)})
            continue
        try:
            result = future.result()
        except Exception as exc:
            failed.append({"agent_id": agent_id, "error": _describe(exc, agent_id in revoke_failed)})
            continue
        burned.append(result | {"sessions_revoked": sorted(set(revoked.get(agent_id, [])) | set(result["sessions_revoked"]))})
    return burned, failed


def _describe(exc: Exception | None, revoke_unconfirmed: bool) -> str:
    """What happened, without exception text (it may carry request details)."""
    if exc is None:
        what = "timed out"
    elif errors.is_throttle(exc):
        what = "rate limited"
    else:
        what = type(exc).__name__
    state = "revoke not confirmed" if revoke_unconfirmed else "instance is revoked"
    return f"{what}; {state}, retry POST /burn"
