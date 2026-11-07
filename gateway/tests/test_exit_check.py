"""REQUEST exit check: known credentials cannot leave through send_message or notify."""
import base64
import json

import interceptor
import shapes
from conftest import events, ledger_rows, put_session, register_credential, request_event, tool_call


def send(session, tool, arguments, trace="Root=1-a"):
    return interceptor.handler(request_event(tool_call(tool, arguments), session["client_id"], trace), None)


def denied_text(out) -> str:
    body = out["mcp"].get("transformedGatewayResponse", {}).get("body")
    return body["result"]["content"][0]["text"] if shapes.is_burn_denial(body) else ""


def test_message_carrying_a_credential_is_blocked(aws):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")

    out = send(session, "send_message", {"to": "agent-beta", "text": f"here is the key: {value}"})

    assert denied_text(out) == "BURN: blocked: the message contains a credential"
    [event] = events(aws)
    assert (event["action"], event["source"], event["tool"]) == ("blocked", "handoff", "send_message")
    assert event["agent_id"] == "agent-alpha" and event["target_agent_id"] == "agent-beta"
    assert event["secret_ref"] == "burn/agent-alpha/api_key" and event["encoding"] == "plain"
    assert value not in json.dumps(event)
    assert ledger_rows(aws, "agent-beta") == {}  # nothing was delivered, nothing inherited


def test_re_encoded_and_rotated_credentials_are_blocked(aws):
    session = put_session(aws)
    old = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha", status="rotated")

    out = send(session, "send_message", {"to": "agent-beta", "text": base64.b64encode(old.encode()).decode()})

    assert denied_text(out).startswith("BURN: blocked")
    [event] = events(aws)
    assert event["encoding"] == "base64"


def test_notify_is_checked_too(aws):
    session = put_session(aws, tools=("notify",))
    value = register_credential(aws, "burn/shared/crm_db_url", "shared", secret_type="other")

    out = send(session, "notify", {"channel": "email:dana@example.com", "text": f"db is {value}"})

    assert denied_text(out).startswith("BURN: blocked")
    [event] = events(aws)
    assert event["source"] == "egress" and event["destination"] == "email:dana@example.com"
    assert event["owner"] == "shared"


def test_clean_message_passes_and_receiver_inherits_the_senders_ledger(aws):
    session = put_session(aws)
    for ref in ("burn/agent-alpha/api_key", "burn/shared/crm_db_url"):
        aws.Table("burn-exposures").put_item(Item={
            "agent_id": "agent-alpha", "secret_ref_or_fp": ref, "secret_ref": ref, "fingerprint": "ab" * 32,
            "secret_type": "api_key", "owner": "agent", "owner_agent_id": "agent-alpha",
            "source": "tool_response", "via": "read_config", "first_seen": "2026-01-01T00:00:00.000Z"})

    arguments = {"to": "agent-beta", "text": "ticket T-1 is about the provider outage"}
    out = send(session, "send_message", arguments)
    send(session, "send_message", arguments)  # interceptor retry of the same call

    assert out["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]["to"] == "agent-beta"
    rows = ledger_rows(aws, "agent-beta")
    assert set(rows) == {"burn/agent-alpha/api_key", "burn/shared/crm_db_url"}
    assert all(r["source"] == "handoff" and r["via"] == "agent-alpha" for r in rows.values())
    inherited = events(aws)
    assert len(inherited) == 2
    assert {(e["action"], e["agent_id"], e["target_agent_id"]) for e in inherited} == {
        ("inherited", "agent-beta", "agent-beta")}


def test_other_tools_arguments_are_not_exit_checked(aws):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")

    out = send(session, "read_config", {"path": value})

    assert "transformedGatewayRequest" in out["mcp"]
