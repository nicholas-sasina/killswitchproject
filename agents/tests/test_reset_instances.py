"""Reset cleans up what swarm instances and canaries leave behind (moto + a fake minter)."""
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reset  # noqa: E402
from burn_contracts import names  # noqa: E402

TABLES = {names.EVENTS_TABLE: "stream", names.EXPOSURES_TABLE: "agent_id", names.SESSIONS_TABLE: "session_id",
          names.MESSAGES_TABLE: "to_agent", names.SCENARIO_TABLE: "pk", names.ENROLL_TOKENS_TABLE: "token_id",
          names.ROLES_TABLE: "name"}
SORT = {names.EVENTS_TABLE: "event_id", names.EXPOSURES_TABLE: "secret_ref_or_fp", names.MESSAGES_TABLE: "msg_id"}

BURNED, EXITED, LIVE, SCENARIO, EVAL = ("agent-worker-aaaaaa", "agent-worker-bbbbbb", "agent-helper-cccccc",
                                        "agent-reader", "agent-eval-7")


class FakeMinter:
    """Remembers who has credentials (IAM user) and canaries, like the real minter would."""

    def __init__(self, agents):
        self.credentials, self.canaries = set(agents), set(agents)
        self.shared, self.calls, self.fail_for = {"burn/shared/db"}, [], set()

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        parts = path.strip("/").split("/")
        if parts[:1] == ["shared"]:
            self.shared.clear()
        elif parts[1] in self.fail_for:
            raise RuntimeError("minter down")
        elif parts[2:] == ["credentials"]:
            self.credentials.discard(parts[1])
        elif parts[2:] == ["canaries"]:
            self.canaries.discard(parts[1])
        else:
            raise AssertionError(path)
        return {"deleted": []}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb, sm = boto3.resource("dynamodb", region_name="us-east-1"), boto3.client("secretsmanager",
                                                                                    region_name="us-east-1")
        for name, pk in TABLES.items():
            keys = [{"AttributeName": pk, "KeyType": "HASH"}]
            attrs = [{"AttributeName": pk, "AttributeType": "S"}]
            if name in SORT:
                keys.append({"AttributeName": SORT[name], "KeyType": "RANGE"})
                attrs.append({"AttributeName": SORT[name], "AttributeType": "S"})
            ddb.create_table(TableName=name, KeySchema=keys, AttributeDefinitions=attrs, BillingMode="PAY_PER_REQUEST")
        sessions = ddb.Table(names.SESSIONS_TABLE)
        for sid, agent, status, role in [("s1", BURNED, "revoked", "worker"), ("s2", EXITED, "exited", "worker"),
                                         ("s3", LIVE, "active", "helper"), ("s4", SCENARIO, "revoked", None),
                                         ("s5", EVAL, "active", "worker")]:
            sessions.put_item(Item={"session_id": sid, "agent_id": agent, "status": status, "client_id": "c" + sid,
                                    **({"role": role} if role else {})})
        ddb.Table(names.SCENARIO_TABLE).put_item(Item={"pk": "meta", "data": {"agent_ids": [SCENARIO]}})
        ddb.Table(names.ENROLL_TOKENS_TABLE).put_item(Item={"token_id": "t1"})
        ddb.Table(names.ROLES_TABLE).put_item(Item={"name": "worker", "tools": []})
        for agent in (BURNED, EXITED, LIVE, SCENARIO, EVAL):
            sm.create_secret(Name=f"burn/{agent}/badge", SecretString="{}")
        sm.create_secret(Name="burn/shared/db", SecretString="x")
        sm.create_secret(Name="burn-system/demo-token", SecretString="x")
        sm.create_secret(Name="someone-else/secret", SecretString="x")
        yield ddb, sm, FakeMinter([BURNED, EXITED, LIVE, SCENARIO, EVAL])


def secret_names(sm):
    return sorted(s["Name"] for s in sm.list_secrets()["SecretList"])


def run(world):
    ddb, sm, minter = world
    reset.reset(minter=minter, secrets=sm, remove_badges_fn=lambda keep_client_ids: None)


def test_instances_and_scenario_agents_end_up_with_nothing_left(world):
    ddb, sm, minter = world
    run(world)
    for agent in (BURNED, EXITED, LIVE, SCENARIO):  # burned, exited, still active, scenario
        assert agent not in minter.credentials and agent not in minter.canaries
    # instance badge secrets are gone; scenario badges (unchanged behaviour), load-test and foreign secrets stay
    assert secret_names(sm) == sorted([f"burn/{EVAL}/badge", f"burn/{SCENARIO}/badge", "burn/shared/db",
                                       "burn-system/demo-token", "someone-else/secret"])


def test_tokens_and_sessions_are_cleared_and_roles_are_kept(world):
    ddb, _, _ = world
    run(world)
    assert ddb.Table(names.ENROLL_TOKENS_TABLE).scan()["Items"] == []
    assert ddb.Table(names.SESSIONS_TABLE).scan()["Items"] == []
    assert [r["name"] for r in ddb.Table(names.ROLES_TABLE).scan()["Items"]] == ["worker"]


def test_load_test_agents_and_foreign_secrets_are_never_touched(world):
    _, sm, minter = world
    run(world)
    assert EVAL in minter.credentials and EVAL in minter.canaries
    assert not any(EVAL in path for _, path in minter.calls)
    assert "someone-else/secret" in secret_names(sm) and "burn-system/demo-token" in secret_names(sm)


def test_a_rerun_after_a_partial_failure_finishes_the_job(world):
    ddb, sm, minter = world
    minter.fail_for = {EXITED}
    with pytest.raises(RuntimeError, match=EXITED):
        run(world)
    # nothing was cleared, so the instance list survives for the retry
    assert len(ddb.Table(names.SESSIONS_TABLE).scan()["Items"]) == 5
    assert ddb.Table(names.ENROLL_TOKENS_TABLE).scan()["Items"] != []
    assert BURNED not in minter.credentials and EXITED in minter.credentials
    minter.fail_for = set()
    run(world)
    assert not (minter.credentials | minter.canaries) - {EVAL}
    assert ddb.Table(names.SESSIONS_TABLE).scan()["Items"] == []


def test_running_again_is_a_no_op(world):
    ddb, sm, minter = world
    run(world)
    before = (secret_names(sm), set(minter.credentials), set(minter.canaries))
    minter.calls.clear()
    run(world)
    assert (secret_names(sm), set(minter.credentials), set(minter.canaries)) == before
    # nothing is left to find (the instance list and the scenario list were cleared), so only the
    # idempotent shared delete is repeated
    assert [c[1] for c in minter.calls] == ["/shared/credentials"]
