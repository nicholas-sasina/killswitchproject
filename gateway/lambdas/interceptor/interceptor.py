"""Gateway interceptor: Burn's checks on every MCP message.

REQUEST (before the tool runs):
  1. Find the caller's badge session from the token's client_id.
  2. Unknown or revoked session -> refuse (a "refused" event, tool never runs).
  3. tools/call: the tool must be on the agent's allowlist, and send_message
     may only go to the agent's allowed recipients.
  4. Inject the real caller id as burn_caller_agent_id (tools get no identity
     from the gateway, and an agent-supplied value must not be trusted).
RESPONSE (before the reply reaches the agent):
  - tools/list is filtered to the agent's allowed tools.
Agents see the plain tool names from contracts/mcp-tools.json; the gateway's
"<target>___" prefix is added and removed here, so it never leaks to agents.
"""
import logging
import os

import sessions
import shapes
from burn_contracts.events import make_event

CALLER_ARG = "burn_caller_agent_id"
ACTIVE = "active"

log = logging.getLogger()
log.setLevel(logging.INFO)


def handler(event, context):
    if shapes.is_response(event):
        return on_response(event)
    return on_request(event)


def on_request(event: dict) -> dict:
    body = shapes.request_body(event)
    if "id" not in body:
        # JSON-RPC notifications (e.g. notifications/initialized) get no reply
        # and run no tool, so there is nothing to authorize.
        return shapes.pass_request(body)

    claims = shapes.jwt_claims(event) or {}
    client_id = claims.get("client_id")
    session = sessions.session_for_client(client_id)
    method = body.get("method")
    tool = _tool_name(body)

    if session is None:
        return refuse(body, None, "unknown badge", tool, detail=f"unknown badge client {client_id}")
    if session.get("status") != ACTIVE:
        return refuse(body, session, "badge revoked", tool)

    if method != "tools/call":
        return shapes.pass_request(body)

    if tool not in set(session.get("tools") or []):
        return refuse(body, session, f"tool not allowed for this agent: {tool}", tool)

    arguments = dict((body.get("params") or {}).get("arguments") or {})
    arguments[CALLER_ARG] = session["agent_id"]  # overwrite whatever the agent sent

    if tool == "send_message":
        recipient = arguments.get("to")
        if recipient not in set(session.get("send_to") or []):
            return refuse(body, session, f"recipient not allowed: {recipient}", tool,
                          target_agent_id=recipient)

    params = {**(body.get("params") or {}), "name": _gateway_tool_name(tool), "arguments": arguments}
    return shapes.pass_request({**body, "params": params})


def on_response(event: dict) -> dict:
    body = shapes.request_body(event)
    reply = shapes.gateway_response(event)
    status, reply_body = reply.get("statusCode", 200), reply.get("body")

    if shapes.is_burn_denial(reply_body) or not isinstance(reply_body, dict):
        return shapes.pass_response(status, reply_body)

    if body.get("method") == "tools/list" and "result" in reply_body:
        session = sessions.session_for_client((shapes.jwt_claims(event) or {}).get("client_id"))
        allowed = set((session or {}).get("tools") or [])
        result = reply_body["result"]
        tools = [{**t, "name": shapes.short_tool_name(t.get("name", ""))} for t in result.get("tools", [])]
        tools = [t for t in tools if t["name"] in allowed]
        reply_body = {**reply_body, "result": {**result, "tools": tools}}

    return shapes.pass_response(status, reply_body)


def refuse(body: dict, session: dict | None, reason: str, tool: str | None,
           detail: str | None = None, target_agent_id: str | None = None) -> dict:
    """Write a "refused" event and answer the call with a Burn denial."""
    agent_id = session["agent_id"] if session else "system"
    method = body.get("method")
    text = detail or reason
    if method != "tools/call":
        text = f"{text} ({method})"
    sessions.put_event(make_event(
        agent_id=agent_id,
        source="gateway",
        action="refused",
        session_id=session.get("session_id") if session else None,
        tool=tool,
        target_agent_id=target_agent_id,
        detail=text[:500],
    ))
    log.info("refused agent=%s method=%s tool=%s reason=%s", agent_id, method, tool, reason)
    return shapes.deny(body, f"access refused: {reason}")


def _gateway_tool_name(tool: str) -> str:
    """The name the gateway routes on: "<target>___<tool>"."""
    return f"{os.environ['BURN_TOOL_TARGET']}{shapes.TOOL_SEPARATOR}{tool}"


def _tool_name(body: dict) -> str | None:
    if body.get("method") != "tools/call":
        return None
    return shapes.short_tool_name((body.get("params") or {}).get("name") or "")
