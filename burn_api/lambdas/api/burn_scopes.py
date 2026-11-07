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
MAX_DEPTH = 50  # lineage depth guard; a real tree is a handful of levels


def _lineage(root_id: str) -> list[dict]:
    found, seen, frontier = [instances.get_session(root_id)], {root_id}, [root_id]
    for _ in range(MAX_DEPTH):
        children = [s for s in db.sessions_for_parents(frontier) if s["session_id"] not in seen]
        if not children:
            break
        found += children
        seen.update(s["session_id"] for s in children)
        frontier = [s["session_id"] for s in children]
    return found


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
    active = {s["session_id"]: s for s in targets(scope, ident) if s["status"] == "active"}

    revoked: dict[str, list[str]] = {}
    for session in active.values():
        if sessions.end_session(session, "revoked"):
            revoked.setdefault(session["agent_id"], []).append(session["session_id"])
    agent_ids = list(dict.fromkeys(s["agent_id"] for s in active.values()))

    burned, failed = [], []
    pool = ThreadPoolExecutor(max_workers=BURN_WORKERS)
    futures = {pool.submit(burn.burn_agent, agent_id, reason): agent_id for agent_id in agent_ids}
    done, pending = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    pool.shutdown(wait=False, cancel_futures=True)
    for future, agent_id in futures.items():
        if future in pending:
            failed.append({"agent_id": agent_id, "error": "timed out; instance is revoked, retry POST /burn"})
            continue
        try:
            result = future.result()
        except Exception as exc:
            # Never put exception text in the response: it may carry request details.
            failed.append({"agent_id": agent_id, "error": _describe(exc)})
            continue
        burned.append(result | {"sessions_revoked": revoked.get(agent_id, [])})
    return {"scope": scope, "id": ident, "burned": burned, "failed": failed}


def _describe(exc: Exception) -> str:
    if errors.is_throttle(exc):
        return "rate limited; instance is revoked, retry POST /burn"
    return f"{type(exc).__name__}; instance is revoked, retry POST /burn"
