"""The real socket path (DNS pinning, TLS verification, redirects, timeouts, size bounds, 429s),
against a local HTTPS server whose certificate comes from a throwaway test CA (trustme).

Nothing leaves the machine: the server listens on 127.0.0.1 and the guard below refuses any
other destination. Production code is unchanged; only its injectable resolver, address check,
CA context and port point at the local server.
"""
from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import trustme

from burn_notify import fixtures, incident as inc_mod
from burn_notify.events import parse
from burn_notify.http import MAX_RESPONSE_BYTES, SafeTransport, UnsafeURL, is_public_address
from burn_notify.policy import classify
from burn_notify.providers import DeliveryError, Destination
from burn_notify.providers.slack import SlackProvider
from burn_notify.providers.telegram import TelegramProvider

from conftest import CHAT, SLACK_URL, TELEGRAM_TOKEN, make_config, make_processor

_real_create_connection = socket.create_connection
_real_connect = socket.socket.connect


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Override of the conftest guard: loopback only."""
    def loopback_only(address, *args, **kwargs):
        if address[0] not in ("127.0.0.1", "::1"):
            raise AssertionError("only loopback connections are allowed in this test module")
        return _real_create_connection(address, *args, **kwargs)

    def connect_loopback_only(self, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
            raise AssertionError("only loopback connections are allowed in this test module")
        return _real_connect(self, address)

    monkeypatch.setattr(socket, "create_connection", loopback_only)
    monkeypatch.setattr(socket.socket, "connect", connect_loopback_only)
    monkeypatch.setattr("burn_notify.providers.base.sleep_for", lambda s: None)


class FakeProviderServer:
    """HTTPS server with scripted responses; records what it received."""

    def __init__(self, ca: trustme.CA, hostname: str):
        self.script: list[tuple[int, dict, bytes, float]] = []
        self.requests: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                body = self.rfile.read(length)
                server.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, headers, payload, delay = server.script.pop(0) if server.script else (200, {}, b"ok", 0)
                if delay:
                    time.sleep(delay)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.handle_error = lambda request, address: None  # client hang-ups are expected in some tests
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ca.issue_cert(hostname).configure_cert(ctx)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def ca():
    return trustme.CA()


@pytest.fixture
def slack_server(ca):
    server = FakeProviderServer(ca, "hooks.slack.com")
    yield server
    server.close()


def trusting(ca) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ca.configure_trust(ctx)
    return ctx


def slack_transport(ca, server, **kw) -> SafeTransport:
    args = dict(exact=SlackProvider.exact_hosts, path_prefixes=SlackProvider.path_prefixes,
                resolver=lambda host: ["127.0.0.1"], address_ok=lambda a: True, context=trusting(ca),
                port=server.port)
    args.update(kw)
    return SafeTransport(**args)


def incident():
    event = parse(fixtures.canary_used())
    return inc_mod.build(event, classify(event), dashboard_url="https://burn.example.com/#/alerts")


SLACK = Destination("slack-sec", "slack", secret_ref="env:SLACK", options={"private_channel_confirmed": True})


def test_real_tls_round_trip(ca, slack_server):
    provider = SlackProvider(slack_transport(ca, slack_server))
    ack = provider.send(provider.build(incident(), SLACK), SLACK, SLACK_URL)
    assert ack.status == 200
    [req] = slack_server.requests
    assert req["path"] == SLACK_URL.split("hooks.slack.com", 1)[1]
    assert req["headers"]["Host"] == "hooks.slack.com"
    assert req["headers"]["Content-Type"] == "application/json"
    assert json.loads(req["body"])["blocks"][0]["text"]["text"].startswith("BURN SECURITY ALERT")


def test_untrusted_certificate_is_refused(ca, slack_server):
    provider = SlackProvider(slack_transport(ca, slack_server, context=ssl.create_default_context()))
    with pytest.raises(DeliveryError) as err:
        provider.send(provider.build(incident(), SLACK), SLACK, SLACK_URL)
    assert err.value.category == "tls_verification_failed"
    assert slack_server.requests == []


def test_certificate_for_another_host_is_refused(ca):
    server = FakeProviderServer(ca, "attacker.example")
    try:
        provider = SlackProvider(slack_transport(ca, server))
        with pytest.raises(DeliveryError) as err:
            provider.send(provider.build(incident(), SLACK), SLACK, SLACK_URL)
        assert err.value.category == "tls_verification_failed"
        assert server.requests == []
    finally:
        server.close()


def test_private_address_refused_before_connecting(ca, slack_server):
    transport = slack_transport(ca, slack_server, address_ok=is_public_address)  # the production rule
    with pytest.raises(UnsafeURL):
        transport.post(SLACK_URL, b"{}", {}, timeout=2)
    assert slack_server.requests == []


def test_redirect_is_not_followed(ca, slack_server):
    slack_server.script.append((302, {"Location": "https://hooks.slack.com/services/elsewhere"}, b"", 0))
    provider = SlackProvider(slack_transport(ca, slack_server))
    with pytest.raises(DeliveryError) as err:
        provider.send(provider.build(incident(), SLACK), SLACK, SLACK_URL)
    assert err.value.category == "redirect_refused"
    assert len(slack_server.requests) == 1


def test_read_timeout_is_transient(ca, slack_server):
    slack_server.script.append((200, {}, b"ok", 1.5))
    provider = SlackProvider(slack_transport(ca, slack_server))
    provider.timeout = 0.3
    with pytest.raises(DeliveryError) as err:
        provider.send(provider.build(incident(), SLACK), SLACK, SLACK_URL)
    assert err.value.category == "network_error" and err.value.retryable


def test_response_body_is_bounded(ca, slack_server):
    slack_server.script.append((500, {}, b"x" * (MAX_RESPONSE_BYTES * 4), 0))
    resp = slack_transport(ca, slack_server).post(SLACK_URL, b"{}", {}, timeout=3)
    assert resp.status == 500 and len(resp.body) == MAX_RESPONSE_BYTES


def test_429_then_success_through_processor(ca, slack_server):
    slack_server.script.append((429, {"Retry-After": "1"}, b"rate_limited", 0))
    slack_server.script.append((200, {}, b"ok", 0))
    proc = make_processor(make_config(telegram=None, discord=None),
                          transports={"slack": slack_transport(ca, slack_server)})
    event = fixtures.canary_used()
    result = proc.process_event(event)
    assert [o.status for o in result.outcomes] == ["delivered"]
    assert len(slack_server.requests) == 2
    assert proc.store.get(event["event_id"], "slack-sec")["attempts"] == 1  # one claim, one in-process retry


def test_server_outage_then_redelivery(ca, slack_server):
    slack_server.script += [(503, {}, b"", 0), (503, {}, b"", 0), (200, {}, b"ok", 0)]
    proc = make_processor(make_config(telegram=None, discord=None),
                          transports={"slack": slack_transport(ca, slack_server)})
    event = fixtures.canary_used()
    assert proc.process_event(event).needs_retry                 # 503, quick retry, 503 -> retrying
    assert not proc.process_event(event, receive_count=2).needs_retry  # SQS redelivery -> delivered
    proc.process_event(event, receive_count=3)                   # a duplicate delivery sends nothing
    assert len(slack_server.requests) == 3
    assert proc.store.get(event["event_id"], "slack-sec")["status"] == "delivered"


def test_telegram_over_real_tls(ca):
    server = FakeProviderServer(ca, "api.telegram.org")
    server.script.append((200, {"Content-Type": "application/json"},
                          b'{"ok":true,"result":{"message_id":321,"chat":{"id":-1}}}', 0))
    try:
        transport = SafeTransport(exact=TelegramProvider.exact_hosts, path_prefixes=TelegramProvider.path_prefixes,
                                  resolver=lambda h: ["127.0.0.1"], address_ok=lambda a: True,
                                  context=trusting(ca), port=server.port)
        provider = TelegramProvider(transport)
        dest = Destination("tg", "telegram", recipient=CHAT, secret_ref="env:TG", options={"allowed_chat_ids": [CHAT]})
        ack = provider.send(provider.build(incident(), dest), dest, TELEGRAM_TOKEN)
        assert ack.provider_ref == "321"
        [req] = server.requests
        assert req["path"] == f"/bot{TELEGRAM_TOKEN}/sendMessage"
        assert TELEGRAM_TOKEN.encode() not in req["body"]
        assert json.loads(req["body"])["chat_id"] == CHAT
    finally:
        server.close()
