"""The smoke and end-to-end checks call the Burn API as the automation person, through the one shared helper."""
import io
import json

import pytest

import smoke
from burn_contracts.automation import AutomationAuthError


def capture(monkeypatch, reply):
    sent = []

    def urlopen(req, timeout):
        sent.append((req.get_method(), req.full_url, {k.lower(): v for k, v in req.header_items()}))
        out = io.BytesIO(json.dumps(reply).encode())
        out.status = 200
        return out

    monkeypatch.setattr(smoke.urllib.request, "urlopen", urlopen)
    return sent


def test_every_smoke_request_carries_the_helpers_header(monkeypatch):
    given = []
    monkeypatch.setattr(smoke, "automation_headers",
                        lambda demo_token=None: given.append(demo_token) or {"authorization": "Bearer automation-id"})
    sent = capture(monkeypatch, {"agents": []})
    monkeypatch.setattr(smoke, "load_env", lambda: {"BURN_API_URL": "https://api.example.invalid"})
    assert smoke.main() == 0  # no scenario loaded: SKIP after the first call
    ((method, url, headers),) = sent
    assert url == "https://api.example.invalid/events?limit=1" and headers["authorization"] == "Bearer automation-id"
    assert "x-burn-demo-token" not in headers and given == [None, None]


def test_the_demo_token_from_dotenv_is_the_fallback_until_there_is_an_automation_person(monkeypatch):
    given = []
    monkeypatch.setattr(smoke, "automation_headers",
                        lambda demo_token=None: given.append(demo_token) or {"x-burn-demo-token": demo_token})
    sent = capture(monkeypatch, {})
    status, _ = smoke.request("https://api.example.invalid", smoke.person_auth({"BURN_DEMO_TOKEN": "from-dotenv"}),
                              "GET", "/events")
    assert status == 200 and sent[0][2]["x-burn-demo-token"] == "from-dotenv" and given == ["from-dotenv"]


def test_no_way_to_sign_in_is_a_skip_that_shows_no_secret(monkeypatch, capsys):
    def fail(demo_token=None):
        raise AutomationAuthError("the automation person could not sign in (NotAuthorizedException)")

    monkeypatch.setattr(smoke, "automation_headers", fail)
    monkeypatch.setattr(smoke, "load_env", lambda: {"BURN_API_URL": "https://api.example.invalid"})
    sent = capture(monkeypatch, {})
    assert smoke.main() == 0 and sent == []
    assert capsys.readouterr().out.startswith("SKIP: cannot sign in to the Burn API (")


def test_e2e_uses_the_same_signed_in_headers(monkeypatch):
    import importlib.util
    pytest.importorskip("boto3")
    path = smoke.pathlib.Path(smoke.__file__).with_name("e2e.py")
    spec = importlib.util.spec_from_file_location("burn_e2e", path)
    e2e = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e2e)
    calls = []
    monkeypatch.setattr(e2e.api, "load_env", lambda: {"BURN_API_URL": "https://api.example.invalid"})
    monkeypatch.setattr(e2e.api, "automation_headers", lambda demo_token=None: {"authorization": "Bearer automation-id"})
    monkeypatch.setattr(e2e.api, "request", lambda base, auth, method, path, body=None: calls.append(auth()) or (200, {}))
    assert e2e.main() == 0  # no scenario loaded
    assert calls == [{"authorization": "Bearer automation-id"}]
