import secrets

import pytest
from botocore.exceptions import ClientError

from burn_agents.cloud import aws_session, retry_while_key_activates
from burn_agents.config import load_config


def test_session_without_own_key_refuses_to_start(monkeypatch):
    # Even with host credentials in the environment, there is no fallback.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "host-" + secrets.token_hex(4))
    with pytest.raises(SystemExit):
        aws_session("", "")
    with pytest.raises(SystemExit):
        aws_session(secrets.token_hex(4), "")


def test_session_uses_only_the_given_key():
    key, secret = secrets.token_hex(8), secrets.token_hex(8)
    creds = aws_session(key, secret).get_credentials().get_frozen_credentials()
    assert (creds.access_key, creds.secret_key) == (key, secret)


def test_missing_key_in_environment_is_a_boot_error():
    env = {k: "x" for k in ("AGENT_ID", "BURN_SCENARIO_FILE", "BURN_GATEWAY_URL", "BURN_TOKEN_URL",
                            "BURN_CLIENT_ID", "BURN_CLIENT_SECRET", "BURN_API_KEY")}
    with pytest.raises(SystemExit):
        load_config(env)


def err(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "GetCallerIdentity")


def test_retry_waits_for_a_new_key_then_succeeds():
    results, sleeps = [err("InvalidClientTokenId"), err("InvalidAccessKeyId"), "ok"], []

    def call():
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    assert retry_while_key_activates(call, sleep=sleeps.append) == "ok"
    assert sleeps == [1, 2]


def test_retry_gives_up_after_about_twenty_seconds_and_ignores_other_errors():
    sleeps = []
    with pytest.raises(ClientError):
        retry_while_key_activates(lambda: (_ for _ in ()).throw(err("InvalidClientTokenId")), sleep=sleeps.append)
    assert 18 <= sum(sleeps) <= 24
    with pytest.raises(ClientError):
        retry_while_key_activates(lambda: (_ for _ in ()).throw(err("AccessDenied")), sleep=lambda s: 1 / 0)
