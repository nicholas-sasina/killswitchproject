"""The demo scripts call the Burn API with the automation person's header, from the one shared helper."""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import common  # noqa: E402


def test_burn_api_sends_the_helpers_header(monkeypatch):
    sent = []
    monkeypatch.setenv("BURN_API_URL", "https://api.example.invalid")
    monkeypatch.setattr(common, "automation_headers", lambda demo_token=None: {"authorization": "Bearer person-id"})
    monkeypatch.setattr(common.httpx, "request",
                        lambda method, url, headers, **kw: sent.append((method, url, headers)) or httpx.Response(201))
    common.burn_api("POST", "/sessions", json={"agent_id": "agent-a"})
    assert sent == [("POST", "https://api.example.invalid/sessions", {"authorization": "Bearer person-id"})]


def test_burn_api_sends_the_stacks_demo_token_until_the_stack_requires_sign_in(monkeypatch):
    from burn_contracts import automation, names
    lookups = []
    monkeypatch.setattr(automation, "_default", automation.AutomationPerson(env={}, client=lambda service: None))
    monkeypatch.setattr(automation.AutomationPerson, "id_token", lambda self: "")  # the stack does not require sign-in yet
    monkeypatch.setattr(common, "secret_value", lambda secret_id: lookups.append(secret_id) or "stack-demo")
    monkeypatch.setenv("BURN_API_URL", "https://api.example.invalid")
    sent = []
    monkeypatch.setattr(common.httpx, "request", lambda method, url, headers, **kw: sent.append(headers) or httpx.Response(201))
    common.burn_api("GET", "/events")
    assert sent == [{names.DEMO_TOKEN_HEADER: "stack-demo"}] and lookups == [names.DEMO_TOKEN_SECRET]
