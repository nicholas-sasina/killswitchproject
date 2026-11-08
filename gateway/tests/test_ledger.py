"""RESPONSE recording: credentials in tool output land in the agent's ledger."""
import base64
import json
import secrets
import string

import interceptor
import ledger
import record
import shapes
from conftest import (events, ledger_rows, put_session, register_credential, response_event,
                      tool_call, tool_result)


def respond(session, text, tool="read_config", trace="Root=1-a", rpc_id=1):
    body = tool_call(tool, {"path": "config/app.env"}, rpc_id)
    return interceptor.handler(response_event(body, session["client_id"], tool_result(text, rpc_id), trace), None)


def test_own_credential_is_recorded_without_its_value(aws):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")

    out = respond(session, json.dumps({"path": "config/app.env", "content": f"KEY={value}\n"}))

    text = out["mcp"]["transformedGatewayResponse"]["body"]["result"]["content"][0]["text"]
    assert value not in text and "{{burn:ref:burn/agent-alpha/api_key}}" in text  # references are the default
    row = ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]
    assert row["owner"] == "agent" and row["owner_agent_id"] == "agent-alpha"
    assert row["source"] == "tool_response" and row["via"] == "read_config" and row["first_seen"]
    assert row["status"] == "referenced" and row["source_ref"] == "read_config:config/app.env"
    [event] = events(aws)
    assert (event["action"], event["source"], event["tool"]) == ("recorded", "tool_response", "read_config")
    assert event["secret_ref"] == "burn/agent-alpha/api_key"
    assert event["detail"] == "own credential seen in read_config output; the agent got a reference"
    assert value not in json.dumps([row, event], default=str)


def test_with_references_off_the_value_passes_through_and_is_recorded(aws):
    session = put_session(aws, references="off")
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")

    out = respond(session, json.dumps({"path": "config/app.env", "content": f"KEY={value}\n"}))

    assert out["mcp"]["transformedGatewayResponse"]["body"]["result"]["content"][0]["text"].count(value) == 1
    row = ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]
    assert row["status"] == "seen" and "report" not in row
    [event] = events(aws)
    assert event["detail"] == "own credential seen in read_config output"
    assert value not in json.dumps([row, event], default=str)


def test_shared_and_other_agents_credentials_are_attributed(aws):
    session = put_session(aws)
    shared = register_credential(aws, "burn/shared/crm_db_url", "shared", secret_type="other")
    theirs = register_credential(aws, "burn/agent-beta/api_key", "agent-beta")

    respond(session, f"notes: {shared} and {theirs}")

    rows = ledger_rows(aws, "agent-alpha")
    assert rows["burn/shared/crm_db_url"]["owner"] == "shared"
    assert rows["burn/agent-beta/api_key"]["owner_agent_id"] == "agent-beta"
    assert {e["detail"] for e in events(aws)} == {
        "shared credential seen in read_config output; the agent got a reference",
        "credential of agent-beta seen in read_config output; the agent got a reference"}


def test_repeat_sightings_update_the_entry_without_a_new_event(aws, monkeypatch):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")

    monkeypatch.setattr(ledger, "utc_now_iso", lambda: "2026-01-01T00:00:00.000Z")
    respond(session, value, trace="Root=1-a", rpc_id=1)
    respond(session, value, trace="Root=1-a", rpc_id=1)  # interceptor retry: same call
    monkeypatch.setattr(ledger, "utc_now_iso", lambda: "2026-01-01T00:05:00.000Z")
    respond(session, value, trace="Root=1-b", rpc_id=2)  # the agent reads it again

    [event] = events(aws)
    assert event["action"] == "recorded"
    row = ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]
    assert row["first_seen"] == "2026-01-01T00:00:00.000Z" and row["last_seen"] == "2026-01-01T00:05:00.000Z"
    assert row["seen_in"] == {"Root=1-a#1", "Root=1-b#2"}


def test_same_credential_seen_by_another_agent_is_recorded_for_it(aws):
    alpha = put_session(aws)
    beta = put_session(aws, agent_id="agent-beta")
    shared = register_credential(aws, "burn/shared/crm_db_url", "shared", secret_type="other")

    respond(alpha, f"db: {shared}", trace="Root=1-a")
    respond(beta, f"db: {shared}", trace="Root=1-b")

    assert sorted(e["agent_id"] for e in events(aws)) == ["agent-alpha", "agent-beta"]
    assert "burn/shared/crm_db_url" in ledger_rows(aws, "agent-beta")


def test_encoded_credential_is_still_recorded(aws):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")

    respond(session, "blob: " + base64.b64encode(value.encode()).decode())

    assert "burn/agent-alpha/api_key" in ledger_rows(aws, "agent-alpha")


def test_unregistered_aws_key_is_recorded_by_fingerprint(aws):
    session = put_session(aws)
    key_id = "AKIA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))

    respond(session, f"aws_access_key_id = {key_id}")

    [(sk, row)] = ledger_rows(aws, "agent-alpha").items()
    assert sk.startswith("fp#") and row["owner"] == "unknown" and row["secret_type"] == "aws_key"
    assert key_id not in json.dumps(row, default=str)


def test_clean_output_and_denials_record_nothing(aws):
    session = put_session(aws)
    respond(session, json.dumps({"tickets": [{"id": "T-1", "subject": "printer on fire"}]}))
    denial = shapes.deny(tool_call("read_config", {}), "access refused: badge revoked")["mcp"]["transformedGatewayResponse"]["body"]
    interceptor.handler(response_event(tool_call("read_config", {}), session["client_id"], denial), None)

    assert ledger_rows(aws, "agent-alpha") == {} and events(aws) == []


def test_output_too_large_to_check_is_withheld(aws, monkeypatch):
    session = put_session(aws)
    monkeypatch.setattr(record.detection, "scan", lambda text: (_ for _ in ()).throw(ValueError("too big")))

    out = respond(session, "x" * 10)

    assert shapes.is_burn_denial(out["mcp"]["transformedGatewayResponse"]["body"])
    [event] = events(aws)
    assert event["action"] == "refused" and event["source"] == "tool_response"
