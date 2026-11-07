import base64
from dataclasses import dataclass, field
import json
import secrets
import socket
from unittest.mock import patch

import pytest
pytest.importorskip("mitmproxy")
from mitmproxy import http
from mitmproxy.test import tflow

from burn_contracts.events import make_event
from burn_contracts.fingerprint import fingerprint
from burn_detect import Scanner, SessionWindow
from proxy.addon import BurnProxy
from proxy.policy import ExitCheck, Settings, public_ip
from test_scanner import Generated, registered


@dataclass(repr=False)
class State:
    key_bytes: bytes
    rows: dict
    agent: str
    session_id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    active: bool = True
    events: list = field(default_factory=list)

    def key(self):
        return self.key_bytes

    def lookup(self, fps):
        return {fp: self.rows[fp] for fp in fps if fp in self.rows}

    def session(self, agent, session):
        return {"status": "active"} if self.active and agent == self.agent and session == self.session_id else None

    def emit(self, **fields):
        event = make_event(source="egress", **fields)
        self.events.append(event)
        return event["event_id"]


def request(checker, agent, *, url="http://sink:8081/webhook", body="", headers=(), window=None):
    return checker.check(url=url, body=body, headers=list(headers), agent_id=agent, window=window or SessionWindow())


@pytest.fixture
def setup(registered):
    scanner, value, _, rows, _ = registered
    agent = next(iter(rows.values()))["owner_agent_id"]
    settings = Settings(gateway_url="https://gateway.example.test/mcp", provider_url="https://provider.example.test")
    return Generated((scanner, value, agent, settings, ExitCheck(scanner, settings)))


def test_exit_check_covers_url_headers_and_body(setup):
    _, value, agent, _, checker = setup
    for kwargs in ({"body": value}, {"url": "http://sink:8081/?token=" + value},
                   {"headers": [("X-Trace", base64.b64encode(value.encode()).decode())]}):
        decision = request(checker, agent, **kwargs)
        assert decision.reason == "credential_detected"
        assert decision.finding.owner_agent_id == agent


def test_provider_credential_exemption_is_narrow(setup):
    _, value, agent, _, checker = setup
    header = [("Authorization", "Bearer " + value)]
    assert request(checker, agent, url="https://provider.example.test/v1", headers=header).reason is None
    assert request(checker, "agent-" + secrets.token_hex(4), url="https://provider.example.test/v1", headers=header).reason
    assert request(checker, agent, headers=header).reason
    assert request(checker, agent, url="https://provider.example.test/v1", headers=header, body=value).reason
    assert request(checker, agent, url="https://provider.example.test/v1",
                   headers=[("Authorization", "Bearer " + value + " " + secrets.token_hex(4))]).reason


def test_sigv4_only_exempts_the_owning_agents_expected_header(setup):
    scanner, _, agent, _, checker = setup
    aws = "A" + "KIA" + secrets.token_hex(8).upper()
    fp = fingerprint(aws, scanner.key)
    row = {"fingerprint": fp, "secret_ref": f"burn/{agent}/aws_key", "owner_agent_id": agent,
           "secret_type": "aws_key", "kind": "exact", "part": "access_key_id", "version": 1}
    previous = scanner.lookup
    scanner.lookup = lambda fps: {**previous(fps), **({fp: row} if fp in fps else {})}
    auth = "AWS4-HMAC-SHA256 Credential=" + aws + "/20261107/us-east-1/sts/aws4_request, SignedHeaders=host;x-amz-date, Signature=" + secrets.token_hex(32)
    url = "https://sts.us-east-1.amazonaws.com/"
    assert request(checker, agent, url=url, headers=[("Authorization", auth)]).reason is None
    assert request(checker, agent, headers=[("Authorization", auth)]).reason
    assert request(checker, "agent-" + secrets.token_hex(4), url=url, headers=[("Authorization", auth)]).reason


def test_boundary_skips_only_body(setup):
    _, value, agent, _, checker = setup
    for url in ("https://gateway.example.test/mcp", "https://bedrock-runtime.us-east-1.amazonaws.com/model"):
        assert request(checker, agent, url=url, body=value).reason is None
        assert request(checker, agent, url=url + "?q=" + value).reason
        assert request(checker, agent, url=url, headers=[("X-Data", value)]).reason


@pytest.mark.parametrize("url,reason", [
    ("http://169.254.169.254/latest/meta-data", "private_destination"),
    ("http://[fd00:ec2::254]/", "private_destination"),
    ("http://127.0.0.1/", "private_destination"),
    ("http://10.0.0.1/", "private_destination"),
    ("http://[::ffff:127.0.0.1]/", "private_destination"),
    ("https://unapproved.example.test/", "destination_not_allowed"),
    ("http://sts.amazonaws.com/", "destination_port_not_allowed"),
])
def test_destination_policy(setup, url, reason):
    _, _, agent, _, checker = setup
    assert request(checker, agent, url=url).reason == reason


def test_private_addresses_and_multicast_are_rejected():
    assert not public_ip("fd00:ec2::254")
    assert not public_ip("224.0.0.1")
    assert not public_ip("::ffff:169.254.169.254")


def flow_for(state, body=b"hello"):
    flow = tflow.tflow()
    flow.request = http.Request.make("POST", "http://sink:8081/webhook", body,
        {"Proxy-Authorization": "Basic " + base64.b64encode((state.agent + ":" + state.session_id).encode()).decode()})
    return flow


def test_proxy_auth_revoke_and_safe_events(setup):
    scanner, value, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    state.lookup = scanner.lookup
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state, value.encode())
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.22.0.5", 8081))]):
        proxy._process(flow)
    assert flow.response.status_code == 403
    assert json.loads(flow.response.content)["reason"] == "credential_detected"
    assert "proxy-authorization" not in flow.request.headers
    event = state.events[-1]
    assert event["source"] == "egress" and event["action"] == "blocked"
    assert event["destination"] == "sink" and event["owner_agent_id"] == agent
    assert value not in json.dumps(state.events)
    state.active = False
    flow = flow_for(state)
    proxy._process(flow)
    assert state.events[-1]["action"] == "refused"
    assert state.events[-1]["session_id"] is None


def test_unknown_session_and_dependency_errors_fail_closed(setup):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state)
    flow.request.headers["Proxy-Authorization"] = "invalid"
    proxy._process(flow)
    assert json.loads(flow.response.content)["reason"] == "session_required"
    state.key = lambda: (_ for _ in ()).throw(RuntimeError("private dependency detail"))
    proxy.checker = None
    flow = flow_for(state)
    proxy._process(flow)
    assert json.loads(flow.response.content)["reason"] == "exit_check_unavailable"
    assert "private dependency detail" not in flow.response.text


def test_host_header_cannot_skip_sink_body_check(setup):
    scanner, value, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    state.lookup = scanner.lookup
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state, value.encode())
    flow.request.headers["Host"] = "gateway.example.test"
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.22.0.5", 8081))]):
        proxy._process(flow)
    assert json.loads(flow.response.content)["reason"] == "credential_detected"


def test_private_dns_destination_produces_blocked_event(setup):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state)
    flow.request.url = "https://provider.example.test/"
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]):
        proxy._process(flow)
    assert json.loads(flow.response.content)["reason"] == "private_destination"
    assert state.events[-1]["action"] == "blocked"
