"""reset.py --orphans-only: badge secrets nothing references any more are deleted, nothing else is (moto)."""
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reset  # noqa: E402
from burn_contracts import names  # noqa: E402

# Session rows: (session_id, agent_id, status, role). A scenario agent's row has no role.
SESSIONS = [("s1", "agent-worker-aaaaaa", "revoked", "worker"), ("s2", "agent-helper-cccccc", "active", "helper"),
            ("s3", "agent-intake", "active", None),  # a scenario agent whose scenario is no longer the loaded one
            ("s4", "agent-eval-7", "active", "worker")]
LOADED = ["agent-reader"]  # in the loaded scenario, no session yet
ORPHANS = ["agent-auditor", "agent-worker-dddddd"]  # badge secrets with no session row and no scenario
KEPT = ["burn/agent-eval-9/badge",  # the eval harness's, never touched
        "burn/agent-auditor/api_key", "burn/agent-auditor/canary_0123456789abcdef01234567",  # not badges
        "burn/agent-auditor/badge_old",  # not a badge secret's name either
        "burn/shared/crm_db_url", "burn-system/demo-token", "burn-system/fingerprint-key", "someone-else/badge"]


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb, sm = boto3.resource("dynamodb", region_name="us-east-1"), boto3.client("secretsmanager",
                                                                                    region_name="us-east-1")
        for name, pk in ((names.SESSIONS_TABLE, "session_id"), (names.SCENARIO_TABLE, "pk")):
            ddb.create_table(TableName=name, KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
                             AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
                             BillingMode="PAY_PER_REQUEST")
        for sid, agent, status, role in SESSIONS:
            ddb.Table(names.SESSIONS_TABLE).put_item(Item={"session_id": sid, "agent_id": agent, "status": status,
                                                           "client_id": "c" + sid, **({"role": role} if role else {})})
        ddb.Table(names.SCENARIO_TABLE).put_item(Item={"pk": "meta", "data": {"agent_ids": LOADED}})
        for agent in [a for _, a, _, _ in SESSIONS] + LOADED + ORPHANS:
            sm.create_secret(Name=f"burn/{agent}/badge", SecretString="{}")
        for name in KEPT:
            sm.create_secret(Name=name, SecretString="x")
        yield ddb, sm


def secret_names(sm):
    found, kwargs = [], {}
    while True:
        page = sm.list_secrets(**kwargs)
        found += [s["Name"] for s in page["SecretList"]]
        if not page.get("NextToken"):
            return sorted(found)
        kwargs["NextToken"] = page["NextToken"]


def sweep(world):
    ddb, sm = world
    return reset.remove_orphaned_badge_secrets(sm, reset.referenced_agent_ids(ddb))


def test_only_badge_secrets_nothing_references_are_deleted(world):
    _, sm = world
    assert sweep(world) == sorted(ORPHANS)
    referenced = [f"burn/{a}/badge" for a in [a for _, a, _, _ in SESSIONS] + LOADED]
    assert secret_names(sm) == sorted(referenced + KEPT)


def test_every_agent_with_a_session_row_is_referenced_whatever_its_status_or_kind(world):
    ddb, _ = world
    assert reset.referenced_agent_ids(ddb) == {a for _, a, _, _ in SESSIONS} | set(LOADED)


def test_sweeping_again_is_a_no_op(world):
    _, sm = world
    sweep(world)
    before = secret_names(sm)
    assert sweep(world) == []
    assert secret_names(sm) == before

