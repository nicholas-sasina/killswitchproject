"""scripts/replay_old_key.py replays only the credential types the agent holds.

Runs on moto: the agent's credentials are rows in burn-credentials and versions in Secrets Manager.
The provider and STS answers are stubbed, so nothing leaves the process.
"""
import json

import pytest
from botocore.exceptions import ClientError

import live
import replay_old_key

AGENT = "agent-claude-01"
OLD_API_KEY = "mpk_live_" + "o" * 32
NEW_API_KEY = "mpk_live_" + "n" * 32
OLD_AWS = {"access_key_id": "AKIAOLDOLDOLDOLDOLD1", "secret_access_key": "old-secret-access-key"}
NEW_AWS = {"access_key_id": "AKIANEWNEWNEWNEWNEW1", "secret_access_key": "new-secret-access-key"}
VALUES = {"api_key": (OLD_API_KEY, NEW_API_KEY), "aws_key": (json.dumps(OLD_AWS), json.dumps(NEW_AWS))}


def hold(tables, secrets_client, secret_type: str, rotated: bool = True) -> None:
    """Record a credential for AGENT the way the minter does; `rotated` leaves an AWSPREVIOUS version."""
    secret_ref = f"burn/{AGENT}/{secret_type}"
    tables["credentials"].put_item(Item={
        "secret_ref": secret_ref, "owner_agent_id": AGENT, "secret_type": secret_type,
        "version": 2 if rotated else 1, "status": "active"})
    old, new = VALUES[secret_type]
    secrets_client.create_secret(Name=secret_ref, SecretString=old)
    if rotated:
        secrets_client.put_secret_value(SecretId=secret_ref, SecretString=new)


@pytest.fixture
def answers(monkeypatch):
    """What the provider and STS say about an old key. Defaults: both refuse it."""
    said = {"provider": (401, {"error": "invalid_api_key"}), "sts": "InvalidClientTokenId"}
    monkeypatch.setattr(live, "whoami", lambda key: said["provider"])
    monkeypatch.setattr(replay_old_key, "sts_error_code", lambda key: said["sts"])
    return said


def run(capsys) -> tuple[int, str]:
    code = replay_old_key.main(["--agent", AGENT])
    out = capsys.readouterr().out
    for old, new in VALUES.values():  # never a value, whatever happened
        for value in (old, new, OLD_AWS["secret_access_key"], NEW_AWS["secret_access_key"]):
            assert value not in out
    return code, out


def test_aws_key_only_passes_without_touching_api_key(tables, secrets_client, answers, capsys):
    hold(tables, secrets_client, "aws_key")
    tables["credentials"].put_item(Item={  # a canary is held too, but never authenticates
        "secret_ref": f"burn-canary/{AGENT}/s1", "owner_agent_id": AGENT, "secret_type": "canary",
        "version": 1, "status": "active"})
    code, out = run(capsys)
    assert code == 0
    assert f"burn/{AGENT}/aws_key previous" in out and "InvalidClientTokenId" in out
    assert "api_key" not in out and "no previous version" not in out


def test_api_key_and_aws_key_both_refused(tables, secrets_client, answers, capsys):
    hold(tables, secrets_client, "api_key")
    hold(tables, secrets_client, "aws_key")
    code, out = run(capsys)
    assert code == 0
    assert f"burn/{AGENT}/api_key previous" in out and "provider -> 401" in out
    assert f"burn/{AGENT}/aws_key previous" in out and "InvalidClientTokenId" in out


def test_held_credential_without_previous_version_fails(tables, secrets_client, answers, capsys):
    hold(tables, secrets_client, "api_key")
    hold(tables, secrets_client, "aws_key", rotated=False)
    code, out = run(capsys)
    assert code == 1
    assert f"burn/{AGENT}/aws_key: no previous version" in out


@pytest.mark.parametrize("accepted", ["api_key", "aws_key"])
def test_accepted_old_key_fails(tables, secrets_client, answers, capsys, accepted):
    hold(tables, secrets_client, "api_key")
    hold(tables, secrets_client, "aws_key")
    if accepted == "api_key":
        answers["provider"] = (200, {"agent_id": AGENT})
    else:
        answers["sts"] = None
    code, out = run(capsys)
    assert code == 1
    if accepted == "aws_key":
        assert "ACCEPTED" in out


def test_agent_with_nothing_on_record_fails(answers, capsys):
    code, out = run(capsys)
    assert code == 1
    assert "no api_key or aws_key on record" in out


def test_sts_error_code_reads_the_refusal(monkeypatch):
    class RefusingSts:
        def get_caller_identity(self):
            raise ClientError({"Error": {"Code": "InvalidClientTokenId", "Message": "x"}}, "GetCallerIdentity")

    monkeypatch.setattr(replay_old_key.boto3, "client", lambda *args, **kwargs: RefusingSts())
    assert replay_old_key.sts_error_code(OLD_AWS) == "InvalidClientTokenId"
