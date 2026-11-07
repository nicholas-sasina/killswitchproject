"""Shared fixtures: moto-backed tables and secrets, and gateway event builders.

Every credential-like value in these tests is random and generated at run time.
"""
import base64
import json
import os
import secrets

import boto3
import pytest
from moto import mock_aws

os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1",
    "BURN_TOOL_TARGET": "burn-tools",
})

import badges  # noqa: E402
import placeholders  # noqa: E402
import sessions  # noqa: E402
import store  # noqa: E402

TABLES = {
    "burn-sessions": ("session_id", None, [("by_client", "client_id"), ("by_agent", "agent_id")]),
    "burn-events": ("stream", "event_id", []),
    "burn-exposures": ("agent_id", "secret_ref_or_fp", []),
    "burn-fingerprints": ("fingerprint", None, []),
    "burn-scenario": ("pk", None, []),
    "burn-messages": ("to_agent", "msg_id", []),
}


def _reset_caches():
    for cached in (sessions._dynamodb, store._dynamodb, placeholders._secrets,
                   badges._cognito, badges.identity_config):
        cached.cache_clear()
    sessions._session_ids.clear()


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setattr(sessions, "_GSI_RETRY_SECONDS", 0)
    with mock_aws():
        _reset_caches()
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        for name, (pk, sk, gsis) in TABLES.items():
            attrs = {pk} | ({sk} if sk else set()) | {g[1] for g in gsis}
            kwargs = {}
            if gsis:
                kwargs["GlobalSecondaryIndexes"] = [
                    {"IndexName": g, "KeySchema": [{"AttributeName": a, "KeyType": "HASH"}],
                     "Projection": {"ProjectionType": "ALL"}} for g, a in gsis]
            ddb.create_table(
                TableName=name, BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
                KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}]
                + ([{"AttributeName": sk, "KeyType": "RANGE"}] if sk else []),
                **kwargs,
            )
        yield boto3.resource("dynamodb", region_name="us-east-1")
        _reset_caches()


def put_session(dynamodb, agent_id="agent-alpha", status="active",
                tools=("list_tickets", "read_config", "send_message"), send_to=("agent-beta",)) -> dict:
    session = {
        "session_id": "s-" + secrets.token_hex(4),
        "agent_id": agent_id,
        "client_id": secrets.token_hex(10),
        "status": status,
        "tools": list(tools),
        "send_to": list(send_to),
    }
    dynamodb.Table("burn-sessions").put_item(Item=session)
    return session


def events(dynamodb) -> list[dict]:
    return dynamodb.Table("burn-events").scan()["Items"]


def fake_token(client_id: str) -> str:
    """Unsigned JWT-shaped token: the interceptor only reads claims (the gateway verifies)."""
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'none'})}.{part({'client_id': client_id, 'scope': 'burn/tools'})}.x"


def rpc(method: str, params: dict | None = None, rpc_id=1) -> dict:
    body = {"jsonrpc": "2.0", "method": method}
    if rpc_id is not None:
        body["id"] = rpc_id
    if params is not None:
        body["params"] = params
    return body


def tool_call(tool: str, arguments: dict, rpc_id=1) -> dict:
    return rpc("tools/call", {"name": f"burn-tools___{tool}", "arguments": arguments}, rpc_id)


def request_event(body: dict, client_id: str, trace="Root=1-test") -> dict:
    return {
        "interceptorInputVersion": "1.0",
        "mcp": {
            "gatewayRequest": {
                "path": "/mcp", "httpMethod": "POST", "body": body,
                "headers": {"Authorization": f"Bearer {fake_token(client_id)}", "X-Amzn-Trace-Id": trace},
            },
            "gatewayResponse": None,
        },
    }


def response_event(body: dict, client_id: str, reply: dict, trace="Root=1-test") -> dict:
    event = request_event(body, client_id, trace)
    event["mcp"]["gatewayResponse"] = {"statusCode": 200, "body": reply, "headers": {}}
    return event


def tool_result(text: str, rpc_id=1) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": {"isError": False, "content": [{"type": "text", "text": text}]}}
