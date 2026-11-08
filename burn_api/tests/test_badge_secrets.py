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


# --- storing a badge waits only for a name that is still being deleted --------------------------

BADGE = {"client_id": "client-1", "client_secret": "not-a-secret", "token_url": "https://example.invalid/token",
         "scope": "burn/tools"}
# What Secrets Manager says while a deleted name is not free yet (put on the old secret, create of a new one).
BEING_DELETED = {"put_secret_value": "You can't perform this operation on the secret because it was marked for "
                                     "deletion.",
                 "create_secret": "You can't create this secret because a secret with this name is already "
                                  "scheduled for deletion."}
OTHER_REFUSAL = "A previous rotation isn't complete. That rotation will be reattempted."


def refusing(monkeypatch, op, message, times):
    """_store_badge's client, with `op` refused (InvalidRequestException) `times` times before it works."""
    import sessions
    client = secrets_manager()
    real, calls = getattr(client, op), []

    def refuse(**kwargs):
        calls.append(kwargs["SecretId" if op == "put_secret_value" else "Name"])
        if len(calls) <= times:
            raise client.exceptions.InvalidRequestException(
                {"Error": {"Code": "InvalidRequestException", "Message": message}}, op)
        return real(**kwargs)

    setattr(client, op, refuse)
    monkeypatch.setattr(sessions.boto3, "client", lambda *args, **kwargs: client)
    return calls


@pytest.mark.parametrize("op", ["put_secret_value", "create_secret"])
def test_storing_a_badge_waits_out_a_name_that_is_still_being_deleted(env, monkeypatch, waits, op):
    import sessions
    if op == "put_secret_value":  # the old secret is there, but going
        secrets_manager().create_secret(Name="burn/agent-a/badge", SecretString="{}")
    calls = refusing(monkeypatch, op, BEING_DELETED[op], times=2)
    assert sessions._store_badge("agent-a", BADGE) == "burn/agent-a/badge"
    assert len(calls) == 3 and waits == [sessions.STORE_BADGE_WAIT_SECONDS] * 2
    assert badge_secret("agent-a")[1] == BADGE


def test_storing_a_badge_gives_up_on_a_name_that_never_frees(env, monkeypatch, waits):
    import sessions
    calls = refusing(monkeypatch, "create_secret", BEING_DELETED["create_secret"], times=1000)
    with pytest.raises(secrets_manager().exceptions.InvalidRequestException):
        sessions._store_badge("agent-a", BADGE)
    assert len(calls) == sessions.STORE_BADGE_ATTEMPTS
    assert waits == [sessions.STORE_BADGE_WAIT_SECONDS] * (sessions.STORE_BADGE_ATTEMPTS - 1)


@pytest.mark.parametrize("op", ["put_secret_value", "create_secret"])
def test_any_other_invalid_request_fails_at_once(env, monkeypatch, waits, op):
    import sessions
    if op == "put_secret_value":
        secrets_manager().create_secret(Name="burn/agent-a/badge", SecretString="{}")
    calls = refusing(monkeypatch, op, OTHER_REFUSAL, times=1000)
    with pytest.raises(secrets_manager().exceptions.InvalidRequestException, match="rotation"):
        sessions._store_badge("agent-a", BADGE)
    assert len(calls) == 1 and waits == []
