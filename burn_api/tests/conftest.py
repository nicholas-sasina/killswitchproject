import secrets
import sys
import types

import boto3
import pytest
from moto import mock_aws

from fake_minter import FakeMinter

# Stand-in for the identity helper that is bundled at deploy time.
badges_stub = types.ModuleType("badges")
badges_stub.created, badges_stub.deleted = [], []


def _create(agent_id, session_id):
    badges_stub.created.append(session_id)
    return {"client_id": "client-" + secrets.token_hex(4), "client_secret": secrets.token_hex(8),
            "token_url": "https://example.invalid/token", "scope": "burn/tools"}


badges_stub.create_badge = _create
badges_stub.delete_badge = lambda client_id: badges_stub.deleted.append(client_id)
sys.modules["badges"] = badges_stub

SCENARIO_AGENTS = {
    "agent-a": {"id": "agent-a", "role": "reader", "tools": ["read_ticket"], "send_to": ["agent-b"],
                "credentials": ["api_key", "aws_key"]},
    "agent-b": {"id": "agent-b", "role": "notifier", "tools": ["notify"], "credentials": ["api_key"]},
}


def _create_tables(ddb):
    def make(name, pk, sk=None, gsis=()):
        keys = [{"AttributeName": pk, "KeyType": "HASH"}]
        if sk:
            keys.append({"AttributeName": sk, "KeyType": "RANGE"})
        attrs = {pk, *([sk] if sk else []), *(g[1] for g in gsis)}
        args = dict(
            TableName=name, KeySchema=keys, BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
        )
        if gsis:
            args["GlobalSecondaryIndexes"] = [
                {"IndexName": i, "KeySchema": [{"AttributeName": k, "KeyType": "HASH"}],
                 "Projection": {"ProjectionType": "ALL"}} for i, k in gsis]
        ddb.create_table(**args)
    make("burn-events", "stream", "event_id", [("by_agent", "agent_id")])
    make("burn-exposures", "agent_id", "secret_ref_or_fp")
    make("burn-credentials", "secret_ref")
    make("burn-sessions", "session_id", gsis=[("by_agent", "agent_id"), ("by_client", "client_id")])
    make("burn-scenario", "pk")
    make("burn-roles", "name")
    make("burn-enroll-tokens", "token_id", gsis=[("by_role", "role")])


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        _create_tables(ddb)
        token = secrets.token_hex(8)
        boto3.client("secretsmanager", region_name="us-east-1").create_secret(
            Name="burn-system/demo-token", SecretString=token)
        res = boto3.resource("dynamodb", region_name="us-east-1")
        res.Table("burn-scenario").put_item(Item={"pk": "meta", "data": {"agent_ids": list(SCENARIO_AGENTS)}})
        for agent_id, agent in SCENARIO_AGENTS.items():
            res.Table("burn-scenario").put_item(Item={"pk": f"agent#{agent_id}", "data": agent})

        import auth, db, minter_client
        auth.reset(); db.reset()
        fake = FakeMinter()
        monkeypatch.setattr(minter_client, "call", fake)
        badges_stub.created.clear(); badges_stub.deleted.clear()
        yield types.SimpleNamespace(token=token, minter=fake, ddb=res, badges=badges_stub)


def call(env, method, path, body=None, query=None, token="ok"):
    import handler
    headers = {"x-burn-demo-token": env.token if token == "ok" else token}
    event = {"requestContext": {"http": {"method": method, "path": path}}, "headers": headers,
             "queryStringParameters": query, "body": None if body is None else __import__("json").dumps(body)}
    out = handler.handler(event)
    return out["statusCode"], __import__("json").loads(out["body"])
