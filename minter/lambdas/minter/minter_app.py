"""Lambda entry point for burn-minter (direct invoke, HTTP-shaped event).

    {"method": "POST", "path": "/agents/agent-x/credentials", "body": {...}, "query": {...}}
 -> {"statusCode": 200, "body": {...}}

Routes are listed in contracts/minter.openapi.yaml.
"""
import json
import logging
import re
from datetime import datetime

import canaries
import credentials
import flags
import key_activity
import shared
from burn_contracts.names import is_agent_id

log = logging.getLogger()
log.setLevel(logging.INFO)

SHARED_NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
AGENT_PATH = re.compile(r"^/agents/(?P<agent_id>[^/]+)/(?P<action>credentials|rotate|key-activity|canaries)$")
SESSION_CANARY_PATH = re.compile(r"^/sessions/(?P<session_id>[^/]+)/canary$")


def _parse_since(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _agent_route(method: str, agent_id: str, action: str, body: dict, query: dict):
    if not is_agent_id(agent_id):
        raise ValueError(f"invalid agent id: {agent_id!r}")
    if (method, action) == ("POST", "credentials"):
        return {"credentials": credentials.issue(agent_id, body.get("types", []))}
    if (method, action) == ("DELETE", "credentials"):
        return {"deleted": credentials.delete(agent_id)}
    if (method, action) == ("POST", "rotate"):
        return credentials.rotate(agent_id, body.get("exposed_refs", []), body.get("evidence"))
    if (method, action) == ("DELETE", "canaries"):
        return {"deleted": canaries.delete_for_agent(agent_id)}
    if (method, action) == ("GET", "key-activity"):
        return key_activity.key_activity(agent_id, _parse_since(query.get("since")))
    return None


def _route(method: str, path: str, body: dict, query: dict):
    match = AGENT_PATH.match(path)
    if match:
        return _agent_route(method, match["agent_id"], match["action"], body, query)
    match = SESSION_CANARY_PATH.match(path)
    if match and method == "POST":
        return canaries.issue(body.get("agent_id", ""), match["session_id"])
    if match and method == "GET":
        return canaries.get(query.get("agent_id", ""), match["session_id"])
    if path == "/flags" and method == "POST":
        return {"flags": flags.raise_many(body.get("secret_refs"), body.get("level"), body.get("evidence"))}
    if path == "/shared/credentials" and method == "POST":
        name = body.get("name", "")
        if not SHARED_NAME_RE.match(name):
            raise ValueError("invalid shared credential name")
        if body.get("type") not in ("other", "api_key"):
            raise ValueError("type must be 'other' or 'api_key'")
        row = shared.issue(name, body["type"], body.get("template", ""))
        return {"secret_ref": row["secret_ref"], "version": int(row["version"])}
    if path == "/shared/credentials" and method == "DELETE":
        return {"deleted": shared.delete_all()}
    return None


def handler(event, context=None):
    body = event.get("body") or {}
    if isinstance(body, str):
        body = json.loads(body)
    try:
        result = _route(event.get("method", ""), event.get("path", ""), body, event.get("query") or {})
    except ValueError as err:
        return {"statusCode": 400, "body": {"error": str(err)}}
    except canaries.NotFound as err:
        return {"statusCode": 404, "body": {"error": str(err)}}
    except Exception:
        log.exception("minter request failed")  # the traceback holds no credential values
        return {"statusCode": 500, "body": {"error": "internal error"}}
    if result is None:
        return {"statusCode": 404, "body": {"error": "no such route"}}
    return {"statusCode": 200, "body": result}
