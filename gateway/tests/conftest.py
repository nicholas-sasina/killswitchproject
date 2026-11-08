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
import detection  # noqa: E402
import ledger  # noqa: E402
import placeholders  # noqa: E402
import sessions  # noqa: E402
import store  # noqa: E402
from burn_contracts import fingerprint as fp  # noqa: E402

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
                   badges._cognito, badges.identity_config,
                   detection._dynamodb, detection.scanner, ledger._table):
        cached.cache_clear()
    sessions._session_ids.clear()
    detection.reset_cache()


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
        boto3.client("secretsmanager", region_name="us-east-1").create_secret(
            Name="burn-system/fingerprint-key", SecretString=secrets.token_hex(32))
        yield boto3.resource("dynamodb", region_name="us-east-1")
        _reset_caches()


def register_credential(dynamodb, secret_ref: str, owner_agent_id: str, secret_type="api_key",
                        status="active") -> str:
    """Mint a random credential and register its fingerprints the way the minter does.

    Returns the value, which only the test holds.
    """
    value = secrets.token_hex(16)
    key = detection.scanner().key
    base = {"secret_ref": secret_ref, "owner_agent_id": owner_agent_id, "secret_type": secret_type,
            "part": "value", "version": 1, "status": status}
    rows = {fp.fingerprint(value, key): {**base, "kind": "exact"}}
    windows = fp.shingle_fingerprints(value, key)
    for index, digest in windows:
        rows.setdefault(digest, {**base, "kind": "shingle", "index": index, "n_shingles": len(windows)})
    with dynamodb.Table("burn-fingerprints").batch_writer() as batch:
        for digest, row in rows.items():
            batch.put_item(Item={"fingerprint": digest, **row})
    return value


def ledger_rows(dynamodb, agent_id: str) -> dict[str, dict]:
    items = dynamodb.Table("burn-exposures").query(
        KeyConditionExpression="agent_id = :a", ExpressionAttributeValues={":a": agent_id})["Items"]
    return {i["secret_ref_or_fp"]: i for i in items}


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
