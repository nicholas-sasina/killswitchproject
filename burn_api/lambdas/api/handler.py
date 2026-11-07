"""Entry point for every Burn API route (API Gateway HTTP API, payload v2).

One Lambda with a tiny router keeps the whole API readable in one place.
"""
import json
import re

from burn_contracts import names

import auth
import burn
import db
import minter_client
import reads
import sessions


def _resp(status: int, body: dict):
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": db.dumps(body)}


def _json_body(event) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _agent_id(value) -> str | None:
    return value if isinstance(value, str) and names.is_agent_id(value) else None


def route(method: str, path: str, query: dict, body: dict):
    """Return (status, body). Raises NotFound / MinterError for the caller to map."""
    if method == "POST" and path == "/sessions":
        agent_id = _agent_id(body.get("agent_id"))
        if not agent_id:
            return 400, {"error": "agent_id is required (agent-xxx)"}
        return 201, sessions.start_session(agent_id)
    if method == "POST" and path == "/burn":
        agent_id = _agent_id(body.get("agent_id"))
        if not agent_id:
            return 400, {"error": "agent_id is required (agent-xxx)"}
        return 200, burn.burn_agent(agent_id, str(body.get("reason") or ""))
    if method == "GET" and path == "/events":
        try:
            limit = int(query.get("limit", 200))
        except ValueError:
            return 400, {"error": "limit must be an integer"}
        return 200, reads.events_feed(query.get("since"), limit)
    if method == "GET":
        if m := re.fullmatch(r"/burn/([^/]+)/blast-radius", path):
            agent_id = _agent_id(m[1])
            return (200, reads.blast_radius(agent_id)) if agent_id else (400, {"error": "bad agent id"})
        if m := re.fullmatch(r"/agents/([^/]+)/ledger", path):
            agent_id = _agent_id(m[1])
            return (200, reads.ledger(agent_id)) if agent_id else (400, {"error": "bad agent id"})
        if m := re.fullmatch(r"/badge/([^/]+)", path):
            return 200, reads.badge(m[1])
    return 404, {"error": "no such route"}


def handler(event, context=None):
    http = event["requestContext"]["http"]
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not auth.is_authorized(headers):
        return _resp(401, {"error": "missing or invalid x-burn-demo-token"})
    try:
        status, body = route(http["method"], http["path"], event.get("queryStringParameters") or {},
                             _json_body(event))
    except sessions.NotFound as exc:
        return _resp(404, {"error": f"not found: {exc}"})
    except minter_client.MinterError as exc:
        return _resp(502, {"error": str(exc)})
    return _resp(status, body)
