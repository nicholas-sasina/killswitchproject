"""Shared test helpers. No test opens a network connection (see the autouse guard)."""
from __future__ import annotations

import socket
import threading

import pytest

from burn_notify import config as config_mod
from burn_notify.http import Response
from burn_notify.processor import Processor
from burn_notify.providers import REGISTRY
from burn_notify.store import MemoryStore

# Fake credentials are assembled at runtime so secret scanners do not match literals.
SLACK_URL = "https://hooks.slack.com/services/" + "T0000/B0000/" + "x" * 24
DISCORD_URL = "https://discord.com/api/webhooks/" + "1234567890/" + "y" * 40
TEAMS_URL = ("https://prod-12.westus.logic.azure.com:443/workflows/" + "a" * 32
             + "/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers&sv=1.0&sig=" + "z" * 30)
TELEGRAM_TOKEN = "123456789:" + "A" * 35
CHAT = "-1001234567890"

SECRETS = {"env:SLACK": SLACK_URL, "env:DISCORD": DISCORD_URL, "env:TEAMS": TEAMS_URL,
           "env:TELEGRAM": TELEGRAM_TOKEN}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr("burn_notify.providers.base.sleep_for", lambda s: None)
    monkeypatch.delenv("BURN_DASHBOARD_ORIGIN", raising=False)


class FakeTransport:
    """Scripted provider responses. `script` items are Response objects or exceptions."""

    def __init__(self, *script, default: Response | None = None):
        self.script = list(script)
        self.default = default or Response(200, {}, b"ok")
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def post(self, url, body, headers, *, timeout):
        with self.lock:
            self.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
            item = self.script.pop(0) if self.script else self.default
        if isinstance(item, BaseException):
            raise item
        return item


class FakeSES:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if self.script else {"MessageId": "ses-msg-1"}
        if isinstance(item, BaseException):
            raise item
        return item


class DictSecrets:
    def __init__(self, values=None):
        self.values = values if values is not None else dict(SECRETS)

    def get(self, ref):
        from burn_notify.secrets import SecretError
        if ref not in self.values:
            raise SecretError("missing")
        return self.values[ref]


def destinations(**overrides):
    base = {
        "slack": {"id": "slack-sec", "provider": "slack", "min_severity": "medium", "secret_ref": "env:SLACK",
                  "options": {"private_channel_confirmed": True}},
        "telegram": {"id": "tg-oncall", "provider": "telegram", "min_severity": "high", "recipient": CHAT,
                     "secret_ref": "env:TELEGRAM", "options": {"allowed_chat_ids": [CHAT]}},
        "discord": {"id": "discord-sec", "provider": "discord", "min_severity": "medium", "secret_ref": "env:DISCORD",
                    "options": {"private_channel_confirmed": True}},
        "teams": {"id": "teams-secops", "provider": "teams", "min_severity": "critical", "secret_ref": "env:TEAMS"},
        "email": {"id": "email-secops", "provider": "email", "min_severity": "critical",
                  "recipient": "secops@example.com", "options": {"from_address": "burn@example.com"}},
    }
    for name, change in overrides.items():
        if change is None:
            base.pop(name)
        else:
            base[name] = {**base[name], **change}
    return list(base.values())


def make_config(doc_overrides=None, **dest_overrides):
    doc = {"dashboard_origin": "https://burn.example.com", "destinations": destinations(**dest_overrides)}
    doc.update(doc_overrides or {})
    return config_mod.parse(doc, environ={})


def make_processor(cfg=None, *, transports=None, ses=None, store=None, clock=None, secrets=None, max_receives=5):
    cfg = cfg or make_config()
    transports = transports or {}
    providers = {name: cls(transport=transports.get(name) or FakeTransport(default=_ok(name)))
                 for name, cls in REGISTRY.items() if name != "email"}
    providers["email"] = REGISTRY["email"](ses_client=ses or FakeSES())
    kwargs = {"clock": clock} if clock else {}
    return Processor(cfg, store or MemoryStore(), providers=providers, secrets=secrets or DictSecrets(),
                     max_receives=max_receives, **kwargs)


def _ok(name):
    return {"telegram": Response(200, {}, b'{"ok":true,"result":{"message_id":42}}'),
            "discord": Response(200, {}, b'{"id":"111222333"}'),
            "teams": Response(202, {}, b"")}.get(name, Response(200, {}, b"ok"))


class Clock:
    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def __call__(self):
        return self.t
