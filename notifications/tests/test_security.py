"""Requirements 18-19: secret redaction, field allowlist, SSRF and URL validation."""
import json
import logging

import pytest

from burn_notify import config as config_mod, fixtures, incident as inc_mod
from burn_notify.events import parse
from burn_notify.http import SafeTransport, UnsafeURL, check_url, is_public_address
from burn_notify.policy import classify
from burn_notify.providers import REGISTRY, Destination
from burn_notify.redact import get_logger, redact
from burn_notify.secrets import SecretError, SecretResolver

from conftest import DISCORD_URL, SLACK_URL, TEAMS_URL, TELEGRAM_TOKEN, FakeSES, FakeTransport, destinations

SENSITIVE_FIELDS = ("detail", "destination", "fingerprint", "secret_ref", "tool", "actor", "encoding",
                    "owner_agent_id", "target_agent_id")


@pytest.mark.parametrize("name", sorted(fixtures.ALL))
def test_no_sensitive_event_field_reaches_any_payload(name):
    event = parse(fixtures.ALL[name]())
    event["detail"] = (event.get("detail") or "") + " LEAKMARKER-detail"
    inc = inc_mod.build(event, classify(event), dashboard_url="https://burn.example.com/#/alerts")
    for raw in destinations():
        dest = config_mod._destination(raw)
        provider = REGISTRY[dest.provider](ses_client=FakeSES()) if dest.provider == "email" \
            else REGISTRY[dest.provider](transport=FakeTransport())
        text = json.dumps(provider.build(inc, dest).body, ensure_ascii=False)
        assert "LEAKMARKER" not in text
        for field in SENSITIVE_FIELDS:
            value = event.get(field)
            allowed = {event["agent_id"], event.get("session_id"), event.get("role")}
            if value and len(value) > 6 and value not in allowed:
                assert value not in text, f"{field} leaked into {dest.provider}"
        assert "203.0.113.9" not in text  # source IP from the provider's detail


def test_incident_has_no_raw_event_fields():
    names = set(inc_mod.Incident.__dataclass_fields__)
    assert not names & set(SENSITIVE_FIELDS)


def test_redaction_patterns():
    aws_key = "AKIA" + "ABCDEFGHIJKLMNOP"
    jwt = "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 10
    text = (f"slack {SLACK_URL} discord {DISCORD_URL} teams {TEAMS_URL} tg bot {TELEGRAM_TOKEN} "
            f"key {aws_key} auth Bearer abcdefghijklmnop jwt {jwt} fp {'ab' * 32}")
    out = redact(text)
    for secret in (SLACK_URL, DISCORD_URL, TELEGRAM_TOKEN, aws_key, "abcdefghijklmnop", jwt, "ab" * 32, "z" * 30):
        assert secret not in out
    assert "logic.azure.com" not in out


def test_logger_redacts(caplog):
    log = get_logger("burn_notify.test")
    with caplog.at_level(logging.INFO, logger="burn_notify.test"):
        log.info("posting to %s", SLACK_URL)
    assert SLACK_URL not in caplog.text and "[REDACTED]" in caplog.text


def test_delivery_records_hold_no_secrets():
    from conftest import make_processor
    from burn_notify.http import Response
    proc = make_processor(transports={"slack": FakeTransport(Response(403, {}, b"invalid_token"))})
    proc.process_event(fixtures.rotation_failed())
    dump = json.dumps(proc.store.list_deliveries(), default=str)
    for secret in (SLACK_URL, DISCORD_URL, TEAMS_URL, TELEGRAM_TOKEN, "-1001234567890", "secops@example.com"):
        assert secret not in dump


def test_secret_resolver_errors_are_value_free():
    resolver = SecretResolver(environ={"X": "hunter2-secret"})
    assert resolver.get("env:X") == "hunter2-secret"
    with pytest.raises(SecretError) as err:
        resolver.get("file:/etc/passwd")
    assert "passwd" not in str(err.value)
    with pytest.raises(SecretError):
        SecretResolver(environ={"X": "v"}, allow_env=False).get("env:X")


def test_secret_resolver_secrets_manager_json_key():
    class SM:
        calls = 0

        def get_secret_value(self, SecretId):
            SM.calls += 1
            return {"SecretString": json.dumps({"webhook_url": SLACK_URL})}

    resolver = SecretResolver(client=SM())
    assert resolver.get("aws-sm:burn-notify/slack#webhook_url") == SLACK_URL
    resolver.get("aws-sm:burn-notify/slack#webhook_url")
    assert SM.calls == 1  # cached


# ---- SSRF ----------------------------------------------------------------------------------
SLACK_RULES = dict(exact=frozenset({"hooks.slack.com"}), path_prefixes=("/services/",))


@pytest.mark.parametrize("url", [
    "http://hooks.slack.com/services/x",                 # not https
    "https://hooks.slack.com.evil.example/services/x",   # suffix trick
    "https://evilhooks.slack.com/services/x",
    "https://user:pw@hooks.slack.com/services/x",        # userinfo
    "https://hooks.slack.com@evil.example/services/x",
    "https://hooks.slack.com:8443/services/x",           # port
    "https://169.254.169.254/latest/meta-data",          # metadata IP literal
    "https://[::1]/services/x",
    "https://hooks.slack.com/api/other",                 # path
    "https://hooks.slack.com/services/x#frag",
    "file:///etc/passwd",
    "gopher://hooks.slack.com/",
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURL) as err:
        check_url(url, **SLACK_RULES)
    assert "evil" not in str(err.value)  # never echo the URL


def test_allowed_url_passes():
    assert check_url(SLACK_URL, **SLACK_RULES)[0] == "hooks.slack.com"
    teams = REGISTRY["teams"]
    assert check_url(TEAMS_URL, suffixes=teams.host_suffixes, path_prefixes=teams.path_prefixes)[0].endswith(
        ".logic.azure.com")


@pytest.mark.parametrize("addr,public", [
    ("10.0.0.5", False), ("172.16.3.1", False), ("192.168.1.1", False), ("127.0.0.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("::1", False), ("fd00::1", False),
    ("::ffff:10.0.0.1", False), ("0.0.0.0", False), ("8.8.8.8", True), ("2606:4700:4700::1111", True),
])
def test_public_address_check(addr, public):
    assert is_public_address(addr) is public


def test_dns_to_private_address_is_refused():
    transport = SafeTransport(resolver=lambda host: ["10.1.2.3"], **SLACK_RULES)
    with pytest.raises(UnsafeURL):
        transport.post(SLACK_URL, b"{}", {}, timeout=1)


def test_mixed_dns_answer_is_refused():
    transport = SafeTransport(resolver=lambda host: ["52.1.2.3", "127.0.0.1"], **SLACK_RULES)
    with pytest.raises(UnsafeURL):
        transport.vet(SLACK_URL)


def test_provider_maps_unsafe_url_to_permanent_failure():
    slack = REGISTRY["slack"](transport=SafeTransport(resolver=lambda h: ["52.1.2.3"], **SLACK_RULES))
    dest = Destination("s", "slack", secret_ref="env:S", options={"private_channel_confirmed": True})
    event = parse(fixtures.canary_used())
    prepared = slack.build(inc_mod.build(event, classify(event), dashboard_url=None), dest)
    from burn_notify.providers import DeliveryError
    with pytest.raises(DeliveryError) as err:
        slack.send(prepared, dest, "https://169.254.169.254/latest/meta-data")
    assert err.value.category == "unsafe_url" and not err.value.retryable


def test_redirects_are_not_followed():
    from burn_notify.http import Response
    from burn_notify.providers import DeliveryError
    p = REGISTRY["teams"](transport=FakeTransport(Response(302, {"location": "http://10.0.0.1/"}, b"")))
    dest = Destination("t", "teams", secret_ref="env:T")
    event = parse(fixtures.canary_used())
    with pytest.raises(DeliveryError) as err:
        p.send(p.build(inc_mod.build(event, classify(event), dashboard_url=None), dest), dest, TEAMS_URL)
    assert err.value.category == "redirect_refused" and len(p.transport.calls) == 1


def test_request_body_size_bounded():
    transport = SafeTransport(resolver=lambda h: ["52.1.2.3"], **SLACK_RULES)
    with pytest.raises(UnsafeURL):
        transport.post(SLACK_URL, b"x" * (64 * 1024 + 1), {}, timeout=1)


@pytest.mark.parametrize("origin", ["http://burn.example.com", "https://burn.example.com/#/evil",
                                    "https://u:p@burn.example.com", "javascript:alert(1)",
                                    "https://burn.example.com/path"])
def test_dashboard_origin_must_be_bare_https(origin):
    with pytest.raises(ValueError):
        inc_mod.dashboard_link(origin)


def test_dashboard_link_uses_existing_alerts_route():
    assert inc_mod.dashboard_link("https://burn.example.com") == "https://burn.example.com/#/alerts"
