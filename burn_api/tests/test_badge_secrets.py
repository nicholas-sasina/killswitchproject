"""The stored badge, burn/<agent_id>/badge: a role instance's goes when its session ends, a scenario agent's stays
for its next session (moto + fake minter)."""
import json

import boto3
import pytest
from conftest import call

WORKER = {"name": "worker", "tools": ["read_ticket"], "credentials": ["api_key"]}


def secrets_manager():
    return boto3.client("secretsmanager", region_name="us-east-1")


def badge_secret(agent_id):
    """(ARN, stored badge) of the agent's badge secret, or None when there is none."""
    client = secrets_manager()
    try:
        return (client.describe_secret(SecretId=f"burn/{agent_id}/badge")["ARN"],
                json.loads(client.get_secret_value(SecretId=f"burn/{agent_id}/badge")["SecretString"]))
    except client.exceptions.ResourceNotFoundException:
        return None


def session_row(env, session_id):
    return env.ddb.Table("burn-sessions").get_item(Key={"session_id": session_id})["Item"]


def join(env):
    if call(env, "GET", "/roles/worker")[0] == 404:
        assert call(env, "POST", "/roles", WORKER)[0] == 201
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 1})[1]["token"]
    status, body = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})
    assert status == 201, body
    return body


@pytest.fixture
def waits(monkeypatch):
    """Every pause _store_badge takes, instead of sleeping."""
    import sessions
    taken = []
    monkeypatch.setattr(sessions.time, "sleep", taken.append)
    return taken


# --- a role instance's badge secret goes with its session ---------------------------------------

@pytest.mark.parametrize("end", ["burn", "exit"])
def test_ending_a_role_instance_deletes_its_badge_secret_and_keeps_its_session_row(env, end):
    instance = join(env)
    agent, sid = instance["agent_id"], instance["session_id"]
    assert badge_secret(agent)[1]["client_id"] == session_row(env, sid)["client_id"]
    before = session_row(env, sid)
    if end == "burn":
        assert call(env, "POST", "/burn", {"agent_id": agent})[0] == 200
    else:
        assert call(env, "POST", f"/instances/{sid}/exit")[0] == 200
    assert badge_secret(agent) is None
    after = session_row(env, sid)
    assert after["status"] == ("revoked" if end == "burn" else "exited")
    assert after["revoked_at" if end == "burn" else "exited_at"]
    # every field the row had is still there, unchanged, apart from the status flip and its stamp
    assert {k: v for k, v in after.items() if k in before and k not in ("status", "exited_at")} == \
        {k: v for k, v in before.items() if k not in ("status", "exited_at")}
    assert before["client_id"] in env.badges.deleted


def test_a_join_refused_after_its_row_was_written_deletes_its_badge_secret(env, monkeypatch):
    import sessions
    assert call(env, "POST", "/roles", WORKER)[0] == 201
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 2})[1]
    real_create_badge = sessions.badges.create_badge

    def revoke_midway(agent_id, session_id):
        call(env, "DELETE", f"/tokens/{token['token_id']}")
        return real_create_badge(agent_id, session_id)

    monkeypatch.setattr(sessions.badges, "create_badge", revoke_midway)
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[0] == 403
    [row] = env.ddb.Table("burn-sessions").scan()["Items"]
    assert row["status"] == "revoked" and badge_secret(row["agent_id"]) is None


def test_a_missing_badge_secret_does_not_disturb_the_revoke(env):
    instance = join(env)
    agent, sid = instance["agent_id"], instance["session_id"]
    secrets_manager().delete_secret(SecretId=f"burn/{agent}/badge", ForceDeleteWithoutRecovery=True)
    status, body = call(env, "POST", "/burn", {"agent_id": agent})
    assert status == 200 and body["sessions_revoked"] == [sid]
    assert session_row(env, sid)["status"] == "revoked"
    assert session_row(env, sid)["client_id"] in env.badges.deleted
    assert any(c[:2] == ("POST", f"/agents/{agent}/rotate") for c in env.minter.calls)  # the burn went on
    assert call(env, "GET", f"/badge/{sid}")[1]["status"] == "revoked"


# --- a scenario agent keeps its badge secret ----------------------------------------------------

def test_ending_a_scenario_agent_keeps_its_badge_secret(env):
    first = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    arn, _ = badge_secret("agent-a")
    assert call(env, "POST", "/burn", {"agent_id": "agent-a"})[0] == 200
    assert session_row(env, first["session_id"])["status"] == "revoked"
    assert badge_secret("agent-a")[0] == arn
    second = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    assert call(env, "POST", f"/instances/{second['session_id']}/exit")[1]["status"] == "exited"
    assert badge_secret("agent-a")[0] == arn


def test_restarting_a_scenario_agent_keeps_its_badge_secret_and_does_not_wait(env, waits):
    first = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    arn, _ = badge_secret("agent-a")
    second = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    assert session_row(env, first["session_id"])["status"] == "exited"
    # the same secret, never deleted, now holding the new session's badge
    assert badge_secret("agent-a")[0] == arn
    assert badge_secret("agent-a")[1]["client_id"] == session_row(env, second["session_id"])["client_id"]
    assert second["badge_ref"] == "burn/agent-a/badge" and waits == []
