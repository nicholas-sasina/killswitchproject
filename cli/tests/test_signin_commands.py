"""burn login / logout / whoami, and how the person-side client authenticates."""
import json
import socket
import time
import urllib.parse
import urllib.request

import pytest

from burn_cli import config, signin
from burn_cli.api import Api, ApiError
from burn_cli.main import run
from fake_cognito import FakeCognito, jwt
from fake_servers import Recorder


@pytest.fixture
def cognito():
    fake = FakeCognito()
    yield fake
    fake.close()


@pytest.fixture
def instances_api():
    server = Recorder(lambda headers, body: (200, {"instances": [], "roles": []}))
    yield server
    server.close()


def burn(capsys, *argv, dotenv=None):
    code = run(list(argv), dotenv=dotenv)
    out = capsys.readouterr()
    return code, out.out, out.err


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def sign_in_env(monkeypatch, cognito, tmp_path):
    monkeypatch.setenv("BURN_PEOPLE_DOMAIN", cognito.url)
    monkeypatch.setenv("BURN_PEOPLE_CLIENT_ID", "client123")
    port = free_port()
    monkeypatch.setattr(signin, "CALLBACK_PORT", port)

    def open_browser(url):
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        cognito.challenge = query["code_challenge"]
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?code=good-code&state={query['state']}", timeout=5).read()
        return True

    monkeypatch.setattr(signin.webbrowser, "open", open_browser)


def test_login_says_who_and_prints_no_token(cognito, capsys, monkeypatch, tmp_path):
    sign_in_env(monkeypatch, cognito, tmp_path)
    code, out, err = burn(capsys, "login")
    cached = signin.load_cache()
    assert code == 0 and out == "Signed in as ada@example.com\n" and err == ""
    for secret in (cached["id_token"], cached["refresh_token"], "good-code"):
        assert secret not in out + err


def test_login_needs_the_pool_settings(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("BURN_PEOPLE_DOMAIN", raising=False)
    monkeypatch.delenv("BURN_PEOPLE_CLIENT_ID", raising=False)
    code, _, err = burn(capsys, "login", dotenv=tmp_path / "none.env")
    assert code == 2 and "BURN_PEOPLE_DOMAIN and BURN_PEOPLE_CLIENT_ID not set" in err


def test_login_reads_the_pool_settings_from_dotenv(cognito, capsys, monkeypatch, tmp_path):
    sign_in_env(monkeypatch, cognito, tmp_path)
    monkeypatch.delenv("BURN_PEOPLE_DOMAIN")
    monkeypatch.delenv("BURN_PEOPLE_CLIENT_ID")
    env = tmp_path / ".env"
    env.write_text(f"BURN_PEOPLE_DOMAIN={cognito.url}\nBURN_PEOPLE_CLIENT_ID=client123\n")
    assert burn(capsys, "login", dotenv=env)[0] == 0


def test_whoami_and_logout(cognito, capsys, monkeypatch, tmp_path):
    assert burn(capsys, "whoami")[1] == "not signed in\n"
    sign_in_env(monkeypatch, cognito, tmp_path)
    burn(capsys, "login")
    code, out, err = burn(capsys, "whoami")
    assert (code, out, err) == (0, "ada@example.com\n", "")
    code, out, _ = burn(capsys, "logout")
    assert code == 0 and out == "Signed out.\n" and signin.load_cache() is None
    assert burn(capsys, "whoami")[1] == "not signed in\n" and burn(capsys, "logout")[1] == "Not signed in.\n"


def test_a_failed_login_exits_1_with_no_token_in_the_message(cognito, capsys, monkeypatch, tmp_path):
    sign_in_env(monkeypatch, cognito, tmp_path)
    cognito.challenge = None
    monkeypatch.setattr(signin.webbrowser, "open", lambda url: urllib.request.urlopen(
        f"http://127.0.0.1:{signin.CALLBACK_PORT}/callback?code=bad&state=" + url.split("state=")[1].split("&")[0], timeout=5).read() and True)
    code, out, err = burn(capsys, "login")
    assert code == 1 and "refused the request" in err and signin.load_cache() is None


# -- the person-side client ---------------------------------------------------------------------
def signed_in(cognito, expires_in=3600):
    cognito.refresh_tokens.add("refresh-0")
    token = jwt({"email": "ada@example.com", "exp": int(time.time()) + expires_in})
    signin.save_cache({"id_token": token, "refresh_token": "refresh-0", "expires_at": int(time.time()) + expires_in,
                       "email": "ada@example.com", "domain": cognito.url, "client_id": "client123"})
    return token


def header(call, name):
    return next((v for k, v in call["headers"].items() if k.lower() == name), None)


def test_commands_send_the_cached_id_token_as_a_bearer_and_never_the_demo_token(cognito, instances_api, capsys, monkeypatch):
    monkeypatch.delenv("BURN_LOCAL_DEV")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", "on")
    monkeypatch.setenv("BURN_API_URL", instances_api.url)
    monkeypatch.setenv("BURN_DEMO_TOKEN", "demo-token-value")  # still set in the environment; must not be sent
    token = signed_in(cognito)
    code, out, err = burn(capsys, "ps")
    (call,) = instances_api.seen
    assert code == 0 and header(call, "authorization") == f"Bearer {token}" and header(call, "x-burn-demo-token") is None
    assert token not in out + err and "demo-token-value" not in out + err


def test_an_expiring_token_is_refreshed_before_the_call(cognito, instances_api, capsys, monkeypatch):
    monkeypatch.delenv("BURN_LOCAL_DEV")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", "on")
    monkeypatch.setenv("BURN_API_URL", instances_api.url)
    stale = signed_in(cognito, expires_in=20)
    code, out, err = burn(capsys, "ps")
    (call,) = instances_api.seen
    sent = header(call, "authorization").removeprefix("Bearer ")
    assert code == 0 and sent != stale and signin.claims(sent)["n"] == 1  # the refreshed token, not the stale one
    assert signin.load_cache()["id_token"] == sent and sent not in out + err and stale not in out + err
    assert [f["grant_type"] for _, f in cognito.requests] == ["refresh_token"]


def test_without_a_sign_in_the_error_says_to_run_login_and_nothing_is_sent(instances_api, capsys, monkeypatch):
    monkeypatch.delenv("BURN_LOCAL_DEV")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", "on")
    monkeypatch.setenv("BURN_API_URL", instances_api.url)
    monkeypatch.setenv("BURN_DEMO_TOKEN", "demo-token-value")
    code, out, err = burn(capsys, "ps")
    assert code == 1 and "not signed in: run `burn login`" in err and instances_api.seen == []


def test_local_development_sends_the_demo_token_and_no_bearer(cognito, instances_api, capsys, monkeypatch):
    monkeypatch.setenv("BURN_LOCAL_DEV", "1")
    monkeypatch.setenv("BURN_API_URL", instances_api.url)
    monkeypatch.setenv("BURN_DEMO_TOKEN", "mock")
    signed_in(cognito)  # even a valid sign-in is ignored in local development
    assert burn(capsys, "ps")[0] == 0
    (call,) = instances_api.seen
    assert header(call, "x-burn-demo-token") == "mock" and header(call, "authorization") is None


@pytest.mark.parametrize("switch", ["off", ""])  # switched off, or not in .env and not readable from SSM
def test_until_the_stack_requires_sign_in_commands_send_the_demo_token_even_when_signed_in(
        cognito, instances_api, capsys, monkeypatch, switch):
    monkeypatch.delenv("BURN_LOCAL_DEV")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", switch)
    monkeypatch.setenv("BURN_API_URL", instances_api.url)
    monkeypatch.setenv("BURN_DEMO_TOKEN", "demo-token-value")
    token = signed_in(cognito)
    assert burn(capsys, "ps")[0] == 0
    (call,) = instances_api.seen
    assert header(call, "x-burn-demo-token") == "demo-token-value" and header(call, "authorization") is None
    assert cognito.requests == []  # the sign-in is not even refreshed
    assert token not in capsys.readouterr().out


def test_the_switch_can_come_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("BURN_LOCAL_DEV")
    monkeypatch.delenv("BURN_DEMO_TOKEN", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("BURN_API_URL=http://x.invalid/\nBURN_PEOPLE_ENABLED=on\n")
    assert config.load(dotenv) == ("http://x.invalid", "")
    dotenv.write_text("BURN_API_URL=http://x.invalid/\nBURN_PEOPLE_ENABLED=off\nBURN_DEMO_TOKEN=d\n")
    assert config.load(dotenv) == ("http://x.invalid", "d")


def test_the_demo_token_is_required_exactly_when_it_is_what_gets_sent(monkeypatch, tmp_path):
    monkeypatch.delenv("BURN_LOCAL_DEV")
    monkeypatch.delenv("BURN_DEMO_TOKEN", raising=False)
    monkeypatch.setenv("BURN_API_URL", "http://x.invalid/")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", "on")
    assert config.load(tmp_path / "none") == ("http://x.invalid", "")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", "off")
    with pytest.raises(config.ConfigError, match="does not require sign-in yet, so it needs BURN_DEMO_TOKEN"):
        config.load(tmp_path / "none")
    monkeypatch.setenv("BURN_LOCAL_DEV", "1")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", "on")  # local development always uses the demo token
    with pytest.raises(config.ConfigError, match="BURN_LOCAL_DEV=1 needs BURN_DEMO_TOKEN"):
        config.load(tmp_path / "none")


@pytest.mark.parametrize("signed", [True, False])
def test_a_401_says_what_to_do(cognito, monkeypatch, signed):
    from fake_servers import Recorder
    server = Recorder(lambda headers, body: (401, {"error": "unauthorized"}))
    try:
        if signed:
            signed_in(cognito)
            api = Api(server.url)
        else:
            api = Api(server.url, "demo-token-value")
        with pytest.raises(ApiError) as exc:
            api.request("GET", "/roles")
    finally:
        server.close()
    hint = "run `burn login` to sign in again" if signed else "now requires sign-in, run `make env`, then `burn login`"
    assert exc.value.status == 401 and hint in str(exc.value) and "demo-token-value" not in str(exc.value)


def test_the_api_error_for_a_missing_sign_in_is_not_retried(monkeypatch):
    monkeypatch.delenv("BURN_LOCAL_DEV")
    with pytest.raises(ApiError) as exc:
        Api("http://127.0.0.1:1").request("GET", "/roles")
    assert exc.value.status == 401 and not exc.value.retryable
