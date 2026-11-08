"""Mock tool handlers against moto DynamoDB and Secrets Manager."""
import json
import secrets
import time
from types import SimpleNamespace

import boto3
import pytest
import store
import tools
from burn_contracts.events import EVENT_RETENTION_DAYS, TTL_ATTRIBUTE


def context(tool: str):
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": f"burn-tools___{tool}"}))


def call(tool: str, caller: str | None = "agent-alpha", **args):
    if caller:
        args["burn_caller_agent_id"] = caller
    return tools.handler(args, context(tool))


@pytest.fixture
def scenario(aws):
    table = aws.Table("burn-scenario")
    for pk, data in {
        "ticket#T-2": {"id": "T-2", "subject": "second", "body": "b2"},
        "ticket#T-1": {"id": "T-1", "subject": "first", "body": "see config/app.env", "priority": 2},
        "file#config/app.env": {"path": "config/app.env", "content": (
            "KEY={{cred:agent-alpha:api_key}}\n"
            "AWS_ID={{cred:agent-alpha:aws_key.access_key_id}}\n"
            "DB={{cred:shared:crm_db_url}}\n"
            "BADGE={{cred:agent-alpha:badge}}\n")},
        "customer#C-1": {"id": "C-1", "name": "Dana Example", "email": "dana@example.com",
                         "notes": "debug: {{cred:shared:crm_db_url}}"},
        "customer#C-2": {"id": "C-2", "name": "Lee Sample", "email": "lee@example.com"},
    }.items():
        table.put_item(Item={"pk": pk, "data": data})

    sm = boto3.client("secretsmanager", region_name="us-east-1")
    values = {
        "api_key": secrets.token_urlsafe(24),
        "access_key_id": secrets.token_hex(10),
        "db": secrets.token_urlsafe(16),
    }
    sm.create_secret(Name="burn/agent-alpha/api_key", SecretString=values["api_key"])
    sm.create_secret(Name="burn/agent-alpha/aws_key", SecretString=json.dumps(
        {"access_key_id": values["access_key_id"], "secret_access_key": secrets.token_hex(20)}))
    sm.create_secret(Name="burn/shared/crm_db_url", SecretString=values["db"])
    sm.create_secret(Name="burn/agent-alpha/badge", SecretString=secrets.token_hex(8))
    return values


def test_list_and_read_tickets(scenario):
    assert call("list_tickets") == {"tickets": [{"id": "T-1", "subject": "first"}, {"id": "T-2", "subject": "second"}]}
    assert call("read_ticket", ticket_id="T-1")["ticket"]["priority"] == 2  # Decimal converted for JSON
    assert "error" in call("read_ticket", ticket_id="T-9")


def test_read_config_fills_placeholders_with_current_values(scenario):
    content = call("read_config", path="config/app.env")["content"]

    assert f"KEY={scenario['api_key']}\n" in content
    assert f"AWS_ID={scenario['access_key_id']}\n" in content
    assert f"DB={scenario['db']}\n" in content
    assert "BADGE=(credential not issued)" in content  # badges are never readable through files
    assert "{{cred:" not in content


def test_read_config_shows_the_rotated_value(scenario):
    rotated = secrets.token_urlsafe(24)
    boto3.client("secretsmanager", region_name="us-east-1").put_secret_value(
        SecretId="burn/agent-alpha/api_key", SecretString=rotated)

    content = call("read_config", path="config/app.env")["content"]

    assert f"KEY={rotated}\n" in content and scenario["api_key"] not in content


def test_query_customers_matches_and_fills_notes(scenario):
    [found] = call("query_customers", query="dana")["customers"]
    assert found["id"] == "C-1" and found["notes"] == f"debug: {scenario['db']}"
    assert call("query_customers", query="")["customers"] == []


def test_messages_use_the_injected_caller_and_inbox_clears(aws):
    sent = call("send_message", caller="agent-alpha", to="agent-beta", text="hello")
    assert sent["delivered"] is True and sent["message_id"]

    assert call("read_inbox", caller="agent-beta")["messages"][0] | {"ts": None} == \
        {"from": "agent-alpha", "text": "hello", "ts": None}
    assert call("read_inbox", caller="agent-beta")["messages"] == []


def test_stored_messages_expire_but_a_read_never_returns_the_ttl(aws):
    # The TTL only reclaims mail a burned agent never collected. It is a Decimal
    # once read back, so it must not reach the JSON tool payload.
    call("send_message", caller="agent-alpha", to="agent-beta", text="hello")
    call("notify", caller="agent-alpha", channel="email:dana@example.com", text="fixed")
    for item in aws.Table("burn-messages").scan()["Items"]:
        assert item[TTL_ATTRIBUTE] > time.time() + (EVENT_RETENTION_DAYS - 1) * 86400

    [taken] = store.take_messages("agent-beta")
    assert TTL_ATTRIBUTE not in taken and taken["text"] == "hello"
    json.dumps(taken)
    assert aws.Table("burn-messages").get_item(
        Key={"to_agent": "agent-beta", "msg_id": taken["msg_id"]}).get("Item") is None


def test_notify_goes_to_the_outbox(aws):
    assert call("notify", channel="email:dana@example.com", text="fixed") == {"sent": True}
    [item] = aws.Table("burn-messages").scan()["Items"]
    assert item["to_agent"] == "outbox" and item["channel"] == "email:dana@example.com"


def test_tools_acting_as_an_agent_need_the_injected_caller(aws):
    assert "error" in call("read_inbox", caller=None)
    assert "error" in call("no_such_tool")


def test_canary_placeholder_is_the_calling_sessions_own_canary(aws):
    """{{canary}} hands each session its OWN decoy: another session's canary is never readable."""
    from burn_contracts.names import canary_secret_ref
    aws.Table("burn-scenario").put_item(Item={"pk": "file#archive/old-provider.bak", "data": {
        "path": "archive/old-provider.bak", "content": "# PROVIDER_API_KEY={{canary}}  # pre-rotation, dead\n"}})
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    mine, theirs = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    sm.create_secret(Name=canary_secret_ref("agent-alpha", "sess-alpha-1"), SecretString=mine)
    sm.create_secret(Name=canary_secret_ref("agent-alpha", "sess-alpha-2"), SecretString=theirs)

    def read(session_id):
        args = {"path": "archive/old-provider.bak", "burn_caller_agent_id": "agent-alpha"}
        if session_id:
            args["burn_caller_session_id"] = session_id
        return tools.handler(args, context("read_config"))["content"]

    assert read("sess-alpha-1") == f"# PROVIDER_API_KEY={mine}  # pre-rotation, dead\n"
    assert theirs not in read("sess-alpha-1")
    assert read("sess-alpha-2") == f"# PROVIDER_API_KEY={theirs}  # pre-rotation, dead\n"
    assert "(credential not issued)" in read(None)           # no session: nothing to fill
    assert "(credential not issued)" in read("sess-none")    # canaries off for that session's role


def test_the_session_argument_never_reaches_a_tool(scenario):
    out = call("read_ticket", ticket_id="T-1", burn_caller_session_id="sess-x")
    assert "burn_caller_session_id" not in json.dumps(out)


def test_canary_in_customer_notes_is_the_calling_sessions_own(aws):
    from burn_contracts.names import canary_secret_ref
    aws.Table("burn-scenario").put_item(Item={"pk": "customer#C-9", "data": {
        "id": "C-9", "name": "Old Vendor", "email": "old@example.com", "notes": "legacy key: {{canary}}"}})
    mine = secrets.token_urlsafe(24)
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(
        Name=canary_secret_ref("agent-alpha", "sess-alpha-1"), SecretString=mine)
    out = tools.handler({"query": "old vendor", "burn_caller_agent_id": "agent-alpha",
                         "burn_caller_session_id": "sess-alpha-1"}, context("query_customers"))
    assert out["customers"][0]["notes"] == f"legacy key: {mine}"
