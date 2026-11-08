"""Entry point for every Burn API route (API Gateway HTTP API, payload v2).

One Lambda with a tiny router keeps the whole API readable in one place.
"""
import json
import re

from burn_contracts import names

import auth
import burn
import burn_scopes
import by_source
import caller
import canary
import db
import errors
import flag_review
import instances
import minter_client
import reads
import roles
import sessions
import tokens


def _resp(status: int, body: dict, headers: dict | None = None):
    return {"statusCode": status, "headers": {"content-type": "application/json", **(headers or {})},
            "body": db.dumps(body)}


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
    if method == "POST" and path == "/instances":
        # The two role forms of POST /sessions on a path of their own; auth has already limited
        # non-admin callers to exactly those two body shapes.
        fields = [body.get(key) for key in ("role", "enroll_token", "parent_session")]
        if "agent_id" in body or not isinstance(fields[0], str) or any(
                f is not None and not isinstance(f, str) for f in fields[1:]):
            return 400, {"error": "send role with exactly one of enroll_token and parent_session"}
        return 201, sessions.start_instance(*fields)
    if method == "POST" and path == "/sessions" and "role" in body and "agent_id" not in body:
        return 201, sessions.start_instance(body.get("role"), body.get("enroll_token"), body.get("parent_session"))
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
    if method == "GET" and path == "/flags":
        return 200, flag_review.list_flags()
    if method == "POST" and path == "/flags/resolve":
        return flag_review.resolve(body)
    if method == "GET" and path == "/events":
        try:
            limit = int(query.get("limit", 200))
        except ValueError:
            return 400, {"error": "limit must be an integer"}
        return 200, reads.events_feed(query.get("since"), limit)
    if method == "GET":
        if m := re.fullmatch(r"/burn/([^/]+)/blast-radius", path):
            agent_id = _agent_id(m[1])
            return (200, reads.blast_radius(agent_id, query.get("since"))) if agent_id else (400, {"error": "bad agent id"})
        if m := re.fullmatch(r"/agents/([^/]+)/ledger", path):
            agent_id = _agent_id(m[1])
            return (200, reads.ledger(agent_id)) if agent_id else (400, {"error": "bad agent id"})
        if m := re.fullmatch(r"/badge/([^/]+)", path):
            return 200, reads.badge(m[1])
    swarm = _swarm_route(method, path, query, body)
    if swarm is not None:
        return swarm
    if method == "POST" and path == "/burn/by-source":
        return 200, by_source.burn_by_source(body)
    return 404, {"error": "no such route"}


def _swarm_route(method: str, path: str, query: dict, body: dict):
    """Roles, join tokens, instances and scoped burns. None when the path is not one of theirs."""
    parts = path.strip("/").split("/")
    match (method, parts):
        case ("GET", ["roles"]):
            return 200, {"roles": roles.list_roles()}
        case ("POST", ["roles"]):
            return 201, roles.create_role(body)
        case ("GET", ["roles", name]):
            return 200, roles.get_role(name)
        case ("PUT", ["roles", name]):
            return 200, roles.put_role(name, body)
        case ("POST", ["roles", name, "tokens"]):
            return 201, tokens.create(name, body)
        case ("DELETE", ["tokens", token_id]):
            return 200, tokens.revoke(token_id)
        case ("GET", ["instances"]):
            return 200, {"instances": instances.list_instances(query.get("role"), query.get("status"))}
        case ("POST", ["instances", session_id, "heartbeat"]):
            return 200, instances.heartbeat(session_id)
        case ("POST", ["instances", session_id, "exit"]):
            return 200, instances.exit_instance(session_id)
        case ("POST", ["burns"]):
            return 200, burn_scopes.burn_scope(str(body.get("scope") or ""), body.get("id"),
                                               str(body.get("reason") or "")[:200])
    return None


# Must match the CORS settings of the HTTP API in infra/burn_infra/burn_api.py.
ALLOWED_ORIGIN = "http://localhost:5173"
ALLOWED_HEADERS = f"content-type,authorization,{names.DEMO_TOKEN_HEADER}"


UNAUTHORIZED = ("missing or invalid credential: sign in and send your token as Authorization: Bearer <jwt>; "
                "an instance's own calls send its session token as Authorization: Bearer bst_...")


def _preflight(headers: dict):
    """Answer a browser CORS preflight. Browsers never attach custom headers to it,
    so it cannot carry the demo token; it reveals nothing, and the real request is still checked."""
    cors = {"access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS", "access-control-allow-headers": ALLOWED_HEADERS,
            "access-control-max-age": "600"}
    if headers.get("origin") == ALLOWED_ORIGIN:
        cors["access-control-allow-origin"] = ALLOWED_ORIGIN
    return {"statusCode": 204, "headers": cors, "body": ""}


def handler(event, context=None):
    if "canary_incident" in event and "requestContext" not in event:
        # Direct async invoke from the mock provider (IAM-only; API Gateway events always
        # carry requestContext). The payload is re-verified, never trusted: see canary.py.
        return canary.handle_incident(event["canary_incident"])
    http = event["requestContext"]["http"]
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if http["method"] == "OPTIONS":
        return _preflight(headers)
    # Parsed once: auth decides on the same body the route then acts on.
    request_body = _json_body(event)
    who = auth.authenticate(event, http["method"], http["path"], headers, request_body)
    if who is None:
        return _resp(401, {"error": UNAUTHORIZED})
    token = caller.use(who)
    try:
        status, body = route(http["method"], http["path"], event.get("queryStringParameters") or {},
                             request_body)
    except sessions.NotFound as exc:
        return _resp(404, {"error": f"not found: {exc}"})
    except errors.ApiError as exc:
        return _resp(exc.status, {"error": exc.message})
    except Exception as exc:
        if errors.is_throttle(exc):  # IAM, Cognito or the minter said slow down; clients retry with backoff
            return _resp(429, {"error": "rate limited; retry with backoff",
                               "retry_after_seconds": errors.RETRY_AFTER_SECONDS},
                         {"retry-after": str(errors.RETRY_AFTER_SECONDS)})
        if isinstance(exc, minter_client.MinterError):
            return _resp(502, {"error": str(exc)})
        raise
    finally:
        caller.reset(token)
    return _resp(status, body)
