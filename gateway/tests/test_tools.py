"""Mock tool handlers against moto DynamoDB and Secrets Manager."""
import json
import secrets
from types import SimpleNamespace

import boto3
import pytest
import tools


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


def test_notify_goes_to_the_outbox(aws):
    assert call("notify", channel="email:dana@example.com", text="fixed") == {"sent": True}
    [item] = aws.Table("burn-messages").scan()["Items"]
    assert item["to_agent"] == "outbox" and item["channel"] == "email:dana@example.com"


def test_tools_acting_as_an_agent_need_the_injected_caller(aws):
    assert "error" in call("read_inbox", caller=None)
    assert "error" in call("no_such_tool")
