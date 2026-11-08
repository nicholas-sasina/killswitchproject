import base64
from dataclasses import dataclass, field
import json
import hashlib
import hmac
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
from proxy.proof import HEADER as PROOF_HEADER, make_session_proof
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


@pytest.mark.parametrize("egress,allowed", [
    (None, True), (["sink"], True), (["SINK."], True), ([], False),
    (["s3.amazonaws.com"], False), ("sink", False), (["*"], False),
    ([None], False), (["https://sink"], False), (["sink:8081"], False),
    ([""], False), ([".."], False), (["-sink"], False), (["sink.."], False),
])
@pytest.mark.parametrize("connect", [False, True])
def test_session_egress_narrows_global_allowlist(setup, egress, allowed, connect):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    state.session = lambda *_: {"status": "active", "egress": egress}
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state)
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.22.0.5", 8081))]):
        proxy._process(flow, connect=connect)
    assert (flow.response is None) == allowed


def test_role_cannot_expand_global_destinations(setup):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    state.session = lambda *_: {"status": "active", "egress": ["outsider.example.test"]}
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state)
    flow.request.url = "https://outsider.example.test/"
    proxy._process(flow)
    assert json.loads(flow.response.content)["reason"] == "destination_not_allowed"


def test_sdk_list_buckets_request_remains_allowed_without_sending_it(setup):
    import boto3
    scanner, _, agent, _, checker = setup
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    class Captured(Exception):
        pass
    captured = []
    def stop(request, **kwargs):
        captured.append(request)
        raise Captured()
    client.meta.events.register("before-send.s3.ListBuckets", stop)
    with pytest.raises(Captured):
        client.list_buckets()
    req = captured[0]
    assert checker.check(url=req.url, method=req.method, body="", headers=[],
                         agent_id=agent, window=SessionWindow()).reason is None


@pytest.mark.parametrize("host", ["s3.amazonaws.com", "s3.us-east-1.amazonaws.com"])
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/", ""), ("PUT", "/bucket/key", "data"),
    ("GET", "/bucket/key", ""), ("DELETE", "/bucket/key", ""),
    ("POST", "/", ""), ("HEAD", "/", ""),
    ("GET", "/?list-type=2", ""), ("GET", "/", "data"),
    ("GET", "/%2f", ""), ("GET", "//", ""),
])
def test_s3_only_allows_list_buckets(setup, host, method, path, body):
    _, _, agent, _, checker = setup
    result = checker.check(url=f"https://{host}{path}", headers=[], body=body,
                           method=method, agent_id=agent, window=SessionWindow())
    assert result.reason == (None if (method, path, body) == ("GET", "/", "") else "s3_operation_not_allowed")


@pytest.mark.parametrize("method,path,headers", [
    ("PUT", "/bucket/key", {}), ("GET", "/", {"Content-Length": "1"}),
    ("GET", "/", {"Transfer-Encoding": "chunked"}),
])
def test_s3_is_refused_before_forwarding_body(setup, method, path, headers):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state)
    flow.request.url = "https://s3.amazonaws.com" + path
    flow.request.method = method
    flow.request.headers.pop("content-length", None)
    flow.request.headers.update(headers)
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("54.231.1.1", 443))]):
        proxy._process(flow, headers_only=True)
    assert flow.response.status_code == 403
    assert json.loads(flow.response.content)["reason"] == "s3_operation_not_allowed"


@pytest.mark.parametrize("headers", [
    [("Host", "bucket.s3.amazonaws.com")],
    [("Host", "s3.amazonaws.com:80")],
    [("Host", "s3.amazonaws.com:0")],
    [("Host", "user@s3.amazonaws.com")],
    [("Host", "s3.amazonaws.com/path")],
    [("Host", "s3.amazonaws.com\t")],
    [("Host", "s3.amazonaws.com"), ("HOST", "s3.amazonaws.com")],
])
def test_http_authority_cannot_select_another_upstream_route(setup, headers):
    _, _, agent, _, checker = setup
    result = request(checker, agent, url="https://s3.amazonaws.com/", headers=headers)
    assert result.reason == "destination_authority_mismatch"


@pytest.mark.parametrize("host", ["s3.amazonaws.com", "S3.AMAZONAWS.COM.", "s3.amazonaws.com:443"])
def test_equivalent_s3_authorities_remain_allowed(setup, host):
    _, _, agent, _, checker = setup
    assert request(checker, agent, url="https://s3.amazonaws.com/", headers=[("Host", host)]).reason is None


@pytest.mark.parametrize("url,reason", [
    ("https://s3.amazonaws.com:0/", "destination_port_not_allowed"),
    ("http://sts.amazonaws.com:443/", "destination_scheme_not_allowed"),
    ("https://user@s3.amazonaws.com/", "destination_authority_mismatch"),
])
def test_url_transport_and_authority_are_unambiguous(setup, url, reason):
    _, _, agent, _, checker = setup
    assert request(checker, agent, url=url).reason == reason


def test_s3_host_header_bypass_is_refused_at_request_headers(setup):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state, body=b"")
    flow.request.url = "https://s3.amazonaws.com/"
    flow.request.method = "GET"
    flow.request.headers["Host"] = "bucket.s3.amazonaws.com"
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("54.231.1.1", 443))]):
        proxy._process(flow, headers_only=True)
    assert json.loads(flow.response.content)["reason"] == "destination_authority_mismatch"


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
    assert json.loads(flow.response.content)["reason"] == "destination_authority_mismatch"
    assert state.events[-1]["action"] == "blocked"
    assert value not in json.dumps(state.events)


def test_tls_sink_cannot_be_downgraded_to_http(setup):
    scanner, _, agent, _, _ = setup
    checker = ExitCheck(scanner, Settings(sink_url="https://sink:8443"))
    assert request(checker, agent, url="http://sink:8443/webhook").reason == "destination_scheme_not_allowed"
    assert request(checker, agent, url="https://sink:8443/webhook").reason is None


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


def test_provider_proof_binds_session_and_timestamp_to_hmac_key():
    key = secrets.token_bytes(32)
    session = "sess.with.dots:and-dashes"
    proof = make_session_proof(session, key, now=1000.9)
    expected = hmac.new(key, f"burn-session-proof:v1:{session}:1000".encode(), hashlib.sha256).hexdigest()
    assert proof == f"v1.{session}.1000.{expected}"
    assert make_session_proof(session + "x", key, now=1000) != proof


@pytest.mark.parametrize("egress", [[], ["provider.example.test"], None])
@pytest.mark.parametrize("connect", [False, True])
def test_valid_role_policy_preserves_gateway_access(setup, egress, connect):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    state.session = lambda *_: {"status": "active", "egress": egress}
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state)
    flow.request.url = settings.gateway_url
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
        proxy._process(flow, connect=connect)
    assert flow.response is None
    assert settings.role_reason("bedrock-runtime.us-east-1.amazonaws.com", {"egress": []}) == "role_destination_not_allowed"
    assert settings.role_reason("gateway.example.test", {"egress": "invalid"}) == "role_policy_invalid"


@pytest.mark.parametrize("session", ["", "x" * 129, "sess\n", "sess/invalid", None])
def test_invalid_provider_proof_session_fails_closed(session):
    with pytest.raises(ValueError):
        make_session_proof(session, secrets.token_bytes(32))


@pytest.mark.parametrize("destination", ["provider", "sink", "gateway"])
def test_only_authorized_provider_requests_get_fresh_proxy_proof(setup, destination):
    scanner, _, agent, settings, _ = setup
    state = State(scanner.key, {}, agent)
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state)
    flow.request.url = {"provider": settings.provider_url, "gateway": settings.gateway_url,
                        "sink": settings.sink_url}[destination]
    flow.request.headers.add(PROOF_HEADER, "agent-forged")
    flow.request.headers.add("X-Burn-Session-Proof", "another-forged")
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
        proxy._process(flow, headers_only=True)
        assert PROOF_HEADER not in flow.request.headers
        assert flow.response is None
        with patch("proxy.proof.time.time", return_value=1000):
            proxy._process(flow)
    assert flow.response is None
    if destination == "provider":
        assert flow.request.headers.get_all(PROOF_HEADER) == [make_session_proof(state.session_id, scanner.key, now=1000)]
    else:
        assert PROOF_HEADER not in flow.request.headers
    assert not state.events


@pytest.mark.parametrize("active,leak", [(False, False), (True, True)])
def test_refused_provider_requests_do_not_get_proof(setup, active, leak):
    scanner, value, agent, settings, _ = setup
    state = State(scanner.key, {}, agent, active=active)
    state.lookup = scanner.lookup
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state, value.encode() if leak else b"safe")
    flow.request.url = settings.provider_url
    flow.request.headers[PROOF_HEADER] = "forged"
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
        proxy._process(flow)
    assert flow.response.status_code == 403
    assert PROOF_HEADER not in flow.request.headers


# --- Burn's own API: heartbeats and exits from inside the agents' network -------------------------

API_URL = "https://abc123apiid.execute-api.us-east-1.amazonaws.com"
API_HOST = "abc123apiid.execute-api.us-east-1.amazonaws.com"


def with_api(settings):
    from dataclasses import replace
    return replace(settings, api_url=API_URL)


def run_through_proxy(state, settings, url, connect, egress):
    state.session = lambda *_: {"status": "active", "egress": egress}
    proxy = BurnProxy(state=state, settings=settings)
    flow = flow_for(state, body=b"")
    flow.request.url = url
    with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
        proxy._process(flow, connect=connect)
    return flow


@pytest.mark.parametrize("path", ["/instances/sess_0123456789abcdef/heartbeat", "/instances/sess_0123456789abcdef/exit"])
@pytest.mark.parametrize("egress", [["provider.example.test"], [], None])
@pytest.mark.parametrize("connect", [False, True])
def test_an_instance_reaches_the_burn_api_even_when_its_role_narrows_egress(setup, path, egress, connect):
    scanner, _, agent, settings, _ = setup
    settings = with_api(settings)
    flow = run_through_proxy(State(scanner.key, {}, agent), settings, API_URL + path, connect, egress)
    assert flow.response is None  # passed on, not refused
    assert API_HOST in settings.allowed_hosts and settings.role_reason(API_HOST, {"egress": []}) is None


@pytest.mark.parametrize("host", ["otherapiid.execute-api.us-east-1.amazonaws.com",
                                  "abc123apiid.execute-api.us-east-1.amazonaws.com.evil.example",
                                  "x.abc123apiid.execute-api.us-east-1.amazonaws.com"])
def test_any_other_host_is_still_refused(setup, host):
    scanner, _, agent, settings, _ = setup
    settings = with_api(settings)
    assert settings.route_reason(host, 443) == "destination_not_allowed"
    flow = run_through_proxy(State(scanner.key, {}, agent), settings, f"https://{host}/instances/s/heartbeat", True, None)
    assert flow.response.status_code == 403
    assert json.loads(flow.response.content)["reason"] == "destination_not_allowed"


def test_the_burn_api_is_reachable_only_on_443_and_over_https(setup):
    _, _, _, settings, _ = setup
    settings = with_api(settings)
    assert settings.route_reason(API_HOST, 8443) == "destination_port_not_allowed"
    assert settings.request_reason(f"http://{API_HOST}/instances/s/exit", "POST", False) == "destination_scheme_not_allowed"


def test_without_burn_api_url_nothing_new_is_allowed(monkeypatch):
    for name, value in {"BURN_GATEWAY_URL": "https://gateway.example.test/mcp",
                        "BURN_PROVIDER_URL": "https://provider.example.test"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("BURN_API_URL", raising=False)
    without = Settings.from_env()
    assert without.api_url == ""
    assert without.allowed_hosts == Settings(gateway_url="https://gateway.example.test/mcp",
                                             provider_url="https://provider.example.test").allowed_hosts
    assert without.route_reason(API_HOST, 443) == "destination_not_allowed"
    assert without.role_reason(API_HOST, {"egress": []}) == "role_destination_not_allowed"
    monkeypatch.setenv("BURN_API_URL", API_URL)
    assert Settings.from_env().allowed_hosts == without.allowed_hosts | {API_HOST}
