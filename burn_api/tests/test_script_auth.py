"""The smoke and end-to-end checks call the Burn API as the automation person, through the one shared helper."""
import importlib.util
import io
import json
import urllib.error

import pytest

import smoke
from burn_contracts import automation, names

API = "https://api.example.invalid"


def capture(monkeypatch, reply, status=200):
    sent = []

    def urlopen(req, timeout):
        sent.append((req.get_method(), req.full_url, {k.lower(): v for k, v in req.header_items()}))
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "refused", {}, io.BytesIO(json.dumps(reply).encode()))
        out = io.BytesIO(json.dumps(reply).encode())
        out.status = status
        return out

    monkeypatch.setattr(smoke.urllib.request, "urlopen", urlopen)
    return sent


class NoAutomationPerson:
    """AWS as seen by the helper on a stack that is on but has no automation person secret."""

    class NotFound(Exception):
        response = {"Error": {"Code": "ResourceNotFoundException"}}

    def __init__(self):
        self.calls = []

    def __call__(self, service):
        return self

    def get_parameter(self, Name):
        self.calls.append(Name)
        return {"Parameter": {"Value": "on" if Name == names.PEOPLE_ENABLED_PARAM else "x"}}

    def get_secret_value(self, SecretId):
        self.calls.append(SecretId)
        raise self.NotFound()


@pytest.fixture
def helper(monkeypatch):
    """The real helper, with fake AWS behind it."""
    def use(aws, env=None):
        monkeypatch.setattr(automation, "_default", automation.AutomationPerson(client=aws, env=env or {}))
    return use


def load_e2e():
    pytest.importorskip("boto3")
    spec = importlib.util.spec_from_file_location("burn_e2e", smoke.pathlib.Path(smoke.__file__).with_name("e2e.py"))
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    return e2e


def test_every_smoke_request_carries_the_helpers_header(monkeypatch):
    given = []
    monkeypatch.setattr(smoke, "automation_headers",
                        lambda env=None: given.append(env) or {"authorization": "Bearer automation-id"})
    sent = capture(monkeypatch, {"agents": []})
    dotenv = {"BURN_API_URL": API, names.PEOPLE_ENABLED_ENV: "on"}
    monkeypatch.setattr(smoke, "load_env", lambda: dotenv)
    assert smoke.main() == 0  # no scenario loaded: SKIP after the first call
    ((method, url, headers),) = sent
    assert url == f"{API}/events?limit=1" and headers["authorization"] == "Bearer automation-id"
    assert "x-burn-demo-token" not in headers and given == [dotenv, dotenv]  # the settings read from .env


def test_with_sign_in_off_the_demo_token_from_dotenv_is_sent_with_no_aws_call(monkeypatch, helper):
    aws = NoAutomationPerson()
    helper(aws)
    sent = capture(monkeypatch, {"agents": []})
    monkeypatch.setattr(smoke, "load_env", lambda: {"BURN_API_URL": API, names.PEOPLE_ENABLED_ENV: "off",
                                                     "BURN_DEMO_TOKEN": "from-dotenv"})
    assert smoke.main() == 0
    assert sent[0][2]["x-burn-demo-token"] == "from-dotenv" and aws.calls == []


@pytest.mark.parametrize("script", ["smoke", "e2e"])
def test_sign_in_on_without_an_automation_person_fails_and_sends_nothing(monkeypatch, helper, capsys, script):
    helper(NoAutomationPerson())
    sent = capture(monkeypatch, {"agents": []})
    monkeypatch.setattr(smoke, "load_env", lambda: {"BURN_API_URL": API, names.PEOPLE_ENABLED_ENV: "on",
                                                     "BURN_DEMO_TOKEN": "d"})
    module = smoke if script == "smoke" else load_e2e()
    if script == "e2e":
        monkeypatch.setattr(module.api, "load_env", smoke.load_env)
    assert module.main() == 1 and sent == []
    out = capsys.readouterr().out
    assert out.startswith("FAIL: cannot sign in to the Burn API (cannot read the automation person's secret")


@pytest.mark.parametrize("script", ["smoke", "e2e"])
def test_a_refused_sign_in_is_a_failure_not_a_skip(monkeypatch, capsys, script):
    capture(monkeypatch, {"error": "unauthorized"}, status=401)
    module = smoke if script == "smoke" else load_e2e()
    scripts = [smoke] + ([module.api] if script == "e2e" else [])
    for m in scripts:
        monkeypatch.setattr(m, "automation_headers", lambda env=None: {"authorization": "Bearer stale"})
        monkeypatch.setattr(m, "load_env", lambda: {"BURN_API_URL": API})
    assert module.main() == 1
    assert "FAIL: the Burn API refused the sign-in (HTTP 401)" in capsys.readouterr().out


def test_only_a_missing_api_url_is_a_skip(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "load_env", lambda: {})
    assert smoke.main() == 0 and capsys.readouterr().out.startswith("SKIP: BURN_API_URL not set")


def test_e2e_uses_the_same_signed_in_headers(monkeypatch):
    e2e = load_e2e()
    calls = []
    monkeypatch.setattr(e2e.api, "load_env", lambda: {"BURN_API_URL": API})
    monkeypatch.setattr(e2e.api, "automation_headers", lambda env=None: {"authorization": "Bearer automation-id"})
    monkeypatch.setattr(e2e.api, "request", lambda base, auth, method, path, body=None: calls.append(auth()) or (200, {}))
    assert e2e.main() == 0  # no scenario loaded
    assert calls == [{"authorization": "Bearer automation-id"}]
