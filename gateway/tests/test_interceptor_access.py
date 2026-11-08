"""Interceptor access decisions: badge status, tool allowlist, recipients, caller identity."""
import interceptor
import shapes
from conftest import events, put_session, request_event, response_event, rpc, tool_call


def forwarded_body(output: dict) -> dict:
    return output["mcp"]["transformedGatewayRequest"]["body"]


def reply_body(output: dict) -> dict:
    return output["mcp"]["transformedGatewayResponse"]["body"]


def test_active_badge_passes_and_caller_id_is_injected(aws):
    session = put_session(aws)
    body = tool_call("read_config", {"path": "a.env", "burn_caller_agent_id": "agent-forged"})

    out = interceptor.handler(request_event(body, session["client_id"]), None)

    params = forwarded_body(out)["params"]
    assert params["arguments"] == {"path": "a.env", "burn_caller_agent_id": "agent-alpha"}
    assert params["name"] == "burn-tools___read_config"
    assert events(aws) == []


def test_plain_tool_names_get_the_gateway_prefix(aws):
    session = put_session(aws)
    body = tool_call("list_tickets", {})
    body["params"]["name"] = "list_tickets"  # what agents send: the contract name

    out = interceptor.handler(request_event(body, session["client_id"]), None)

    assert forwarded_body(out)["params"]["name"] == "burn-tools___list_tickets"


def test_revoked_badge_is_refused_on_the_next_call(aws):
    session = put_session(aws)
    body = tool_call("list_tickets", {})
    assert "transformedGatewayRequest" in interceptor.handler(request_event(body, session["client_id"]), None)["mcp"]

    aws.Table("burn-sessions").update_item(
        Key={"session_id": session["session_id"]},
        UpdateExpression="SET #s = :r", ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":r": "revoked"},
    )
    out = interceptor.handler(request_event(body, session["client_id"]), None)

    result = reply_body(out)["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"].startswith("BURN: access refused: badge revoked")
    [event] = events(aws)
    assert event["action"] == "refused" and event["source"] == "gateway"
    assert event["agent_id"] == "agent-alpha" and event["session_id"] == session["session_id"]
    assert event["tool"] == "list_tickets"


def test_an_exited_sessions_badge_is_refused_like_a_revoked_one(aws):
    """A restart ends the previous session as exited, not revoked; only an active session may call."""
    session = put_session(aws)
    body = tool_call("list_tickets", {})
    aws.Table("burn-sessions").update_item(
        Key={"session_id": session["session_id"]},
        UpdateExpression="SET #s = :e", ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":e": "exited"},
    )
    out = interceptor.handler(request_event(body, session["client_id"]), None)

    result = reply_body(out)["result"]
    assert result["isError"] is True and result["content"][0]["text"].startswith("BURN: access refused")
    assert "transformedGatewayRequest" not in out["mcp"]
    [event] = events(aws)
    assert event["action"] == "refused" and event["session_id"] == session["session_id"]


def test_unknown_badge_is_refused_for_any_method(aws):
    out = interceptor.handler(request_event(rpc("tools/list"), "client-nobody-knows"), None)

    error = reply_body(out)["error"]
    assert error["message"].startswith("BURN: access refused: unknown badge")
    [event] = events(aws)
    assert event["agent_id"] == "system" and event["action"] == "refused"
    assert "tools/list" in event["detail"]


def test_tool_outside_allowlist_is_refused(aws):
    session = put_session(aws, tools=("list_tickets",))

    out = interceptor.handler(request_event(tool_call("read_config", {"path": "x"}), session["client_id"]), None)

    assert reply_body(out)["result"]["content"][0]["text"] == \
        "BURN: access refused: tool not allowed for this agent: read_config"
    [event] = events(aws)
    assert event["tool"] == "read_config"


def test_send_message_only_to_allowed_recipients(aws):
    session = put_session(aws, send_to=("agent-beta",))
    client = session["client_id"]

    ok = interceptor.handler(request_event(tool_call("send_message", {"to": "agent-beta", "text": "hi"}), client), None)
    bad = interceptor.handler(request_event(tool_call("send_message", {"to": "agent-gamma", "text": "hi"}), client), None)

    assert forwarded_body(ok)["params"]["arguments"]["to"] == "agent-beta"
    assert shapes.is_burn_denial(reply_body(bad))
    [event] = events(aws)
    assert event["target_agent_id"] == "agent-gamma"


def test_notifications_pass_without_a_lookup(aws):
    body = rpc("notifications/initialized", rpc_id=None)
    out = interceptor.handler(request_event(body, "client-nobody-knows"), None)
    assert forwarded_body(out) == body
    assert events(aws) == []


def test_tools_list_shows_only_allowed_tools_with_plain_names(aws):
    session = put_session(aws, tools=("list_tickets",))
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [
        {"name": "burn-tools___list_tickets"}, {"name": "burn-tools___read_config"}]}}

    out = interceptor.handler(response_event(rpc("tools/list"), session["client_id"], reply), None)

    assert [t["name"] for t in reply_body(out)["result"]["tools"]] == ["list_tickets"]


def test_burn_denials_pass_through_response_untouched(aws):
    session = put_session(aws)
    denial = reply_body(shapes.deny(tool_call("list_tickets", {}), "access refused: badge revoked"))

    out = interceptor.handler(response_event(tool_call("list_tickets", {}), session["client_id"], denial), None)

    assert reply_body(out) == denial
