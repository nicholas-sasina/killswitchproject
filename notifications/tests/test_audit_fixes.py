"""Regression tests for the bugs found in the PR #113 audit."""
import json

import pytest

from burn_notify import fixtures, handler, incident as inc_mod
from burn_notify.events import event_time_ms, parse
from burn_notify.http import Response, UnsafeURL, check_url
from burn_notify.policy import Severity, classify
from burn_notify.providers import REGISTRY, Destination
from burn_notify.providers.discord import DiscordProvider
from burn_notify.providers.slack import SlackProvider

from conftest import Clock, FakeSES, FakeTransport, destinations, make_config, make_processor


# ---- classification of writers added on main (replace on burn) and the provider's report ----------
def test_replacement_not_recorded_is_not_called_a_credential_flag():
    result = classify(parse(fixtures.replacement_not_recorded()))
    assert result.category == "replacement_failed" and result.severity == Severity.MEDIUM
    assert "credential" not in result.title.lower()
    assert "Restart its task by hand" in result.summary


def test_provider_reported_flag_wording():
    result = classify(parse(fixtures.provider_reported()))
    assert result.category == "flag_reported" and result.severity == Severity.HIGH
    assert "presented at a monitored provider" in result.summary
    assert "this agent" not in result.summary


# ---- Slack Workflow Builder triggers take a different payload -----------------------------------
def test_slack_workflow_trigger_urls_are_refused():
    with pytest.raises(UnsafeURL):
        check_url("https://hooks.slack.com/triggers/T0/1/abc", exact=SlackProvider.exact_hosts,
                  path_prefixes=SlackProvider.path_prefixes)


# ---- Discord limits apply after escaping --------------------------------------------------------
def test_discord_field_limits_hold_after_escaping():
    from dataclasses import replace
    event = parse(fixtures.canary_used())
    inc = inc_mod.build(event, classify(event), dashboard_url=None)
    inc = replace(inc, summary="*_" * 3000, facts=(("Status", "_" * 1000),))
    body = DiscordProvider(FakeTransport()).build(inc, Destination(
        "d", "discord", secret_ref="env:D", options={"private_channel_confirmed": True})).body
    embed = body["embeds"][0]
    assert len(embed["description"]) <= 4096
    assert all(len(f["value"]) <= 1024 and len(f["name"]) <= 256 for f in embed["fields"])
    assert "content" not in body


# ---- synthetic sends are unmistakable ----------------------------------------------------------
@pytest.mark.parametrize("raw", destinations(), ids=lambda d: d["provider"])
def test_test_notice_labels_every_provider(raw):
    from burn_notify import config as config_mod
    dest = config_mod._destination(raw)
    event = parse(fixtures.canary_used())
    inc = inc_mod.build(event, classify(event), dashboard_url=None, notice="SYNTHETIC TEST - not a real incident")
    provider = REGISTRY[dest.provider](ses_client=FakeSES()) if dest.provider == "email" \
        else REGISTRY[dest.provider](transport=FakeTransport())
    text = json.dumps(provider.build(inc, dest).body, ensure_ascii=False)
    assert "[TEST]" in text and "SYNTHETIC TEST" in text


# ---- containment summary accuracy --------------------------------------------------------------
def test_summary_ignores_results_from_before_the_revoke():
    clock = Clock()
    proc = make_processor(make_config(telegram=None, discord=None), clock=clock)
    proc.process_event(fixtures.rotation_failed(offset_ms=-60_000))   # an earlier incident, same agent
    proc.process_event(fixtures.rotated(offset_ms=-30_000))           # and an older rotation
    proc.process_event(fixtures.canary_revoked())
    proc.process_event(fixtures.rotated(offset_ms=5))
    clock.t += 120
    proc.flush_windows()
    [(dest, summary)] = proc.flushed
    facts = dict(summary.facts)
    assert facts["Owned credentials"].startswith("Rotated where confirmed (1 ")
    assert summary.severity == Severity.HIGH  # the earlier failure is not this containment's outcome


def test_summary_is_sent_even_when_nothing_was_recorded():
    clock = Clock()
    slack = FakeTransport()
    proc = make_processor(make_config(telegram=None, discord=None), transports={"slack": slack}, clock=clock)
    proc.process_event(fixtures.burn_revoked())
    clock.t += 120
    proc.flush_windows()
    [(dest, summary)] = proc.flushed
    assert dict(summary.facts)["Owned credentials"].startswith("No rotation recorded since the revoke")
    assert len(slack.calls) == 2


def test_summary_failure_is_retried_on_the_next_flush():
    clock = Clock()
    slack = FakeTransport(Response(200, {}, b"ok"), Response(503, {}, b""), Response(503, {}, b""))
    proc = make_processor(make_config(telegram=None, discord=None), transports={"slack": slack}, clock=clock)
    proc.process_event(fixtures.canary_revoked())
    clock.t += 120
    first = proc.flush_windows()
    assert [o.status for o in first] == ["retrying"]
    clock.t += 60
    second = proc.flush_windows()
    assert [o.status for o in second] == ["delivered"]
    assert proc.flush_windows() == []


def test_aggregate_summary_failure_is_retried():
    clock = Clock()
    slack = FakeTransport(*([Response(200, {}, b"ok")] * 3), Response(503, {}, b""), Response(503, {}, b""))
    proc = make_processor(make_config(telegram=None, discord=None), transports={"slack": slack}, clock=clock)
    for i in range(6):
        proc.process_event(fixtures.blocked(offset_ms=i))
    clock.t += 400
    assert [o.status for o in proc.flush_windows()] == ["retrying"]
    clock.t += 60
    assert [o.status for o in proc.flush_windows()] == ["delivered"]


# ---- Lambda deadline ---------------------------------------------------------------------------
def test_destinations_that_do_not_fit_the_deadline_are_left_unclaimed():
    t = {"now": 100.0}
    slack = FakeTransport()
    proc = make_processor(transports={"slack": slack})
    proc.monotonic = lambda: t["now"]
    event = fixtures.canary_used()
    result = proc.process_event(event, deadline=105.0)  # 5 s left: nothing fits
    assert result.needs_retry and {o.category for o in result.outcomes} == {"deadline"}
    assert proc.store.list_deliveries() == [] and slack.calls == []
    assert not proc.process_event(event, deadline=None).needs_retry


def test_handler_derives_deadline_from_lambda_context():
    seen = {}

    class Proc:
        def process_record(self, record, *, receive_count, deadline):
            seen["deadline"] = deadline
            from burn_notify.processor import EventResult
            return EventResult("evt")

    class Ctx:
        def get_remaining_time_in_millis(self):
            return 60_000

    handler.queue_handler({"Records": [{"messageId": "m", "body": "{}"}]}, Ctx(), proc=Proc())
    import time
    assert 50 < seen["deadline"] - time.monotonic() <= 57


def test_event_time_from_ulid():
    event = fixtures.canary_used()
    from datetime import datetime, timezone
    ts = datetime.strptime(event["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp() * 1000
    assert abs(event_time_ms(event["event_id"]) - ts) < 2_000
    assert event_time_ms("not-an-id") is None


def test_teams_identifiers_render_without_backslashes():
    from burn_notify import escape
    assert escape.teams("evt_01M6X0QPK47RD91Z0P8FCJ3QTC") == "evt_01M6X0QPK47RD91Z0P8FCJ3QTC"
    assert escape.teams("sess_5b1e0c9a7d2f4e61") == "sess_5b1e0c9a7d2f4e61"
    assert escape.teams("[TEST] BURN SECURITY ALERT") == "[TEST] BURN SECURITY ALERT"
    assert "](" not in escape.teams("[click](https://evil.example)")
    assert escape.teams("_a_ *b*") == "\\_a\\_ \\*b\\*"


def test_deployment_test_envelope_is_always_labelled():
    import io
    from burn_notify import cli
    out = io.StringIO()
    cli.main(["test-message", "--fixture", "canary_revoked"], out)
    body = out.getvalue().strip()
    clock = Clock()
    slack = FakeTransport()
    proc = make_processor(make_config(telegram=None, discord=None), transports={"slack": slack}, clock=clock)
    proc.process_record({"messageId": "m", "receiptHandle": "r", "body": body})
    assert "[TEST]" in slack.calls[0]["body"].decode()
    clock.t += 120
    proc.flush_windows()  # the follow-up summary of a test message is labelled too
    assert len(slack.calls) == 2 and "[TEST]" in slack.calls[1]["body"].decode()


def test_plain_records_are_not_labelled():
    slack = FakeTransport()
    proc = make_processor(make_config(telegram=None, discord=None), transports={"slack": slack})
    proc.process_record(fixtures.stream_record(fixtures.canary_used()))
    assert "[TEST]" not in slack.calls[0]["body"].decode()


def test_deployment_test_message_does_not_touch_flag_ordering():
    import io
    from burn_notify import cli
    out = io.StringIO()
    cli.main(["test-message", "--fixture", "flagged_reported"], out)
    proc = make_processor()
    proc.process_record({"messageId": "m", "receiptHandle": "r", "body": out.getvalue().strip()})
    real_later = fixtures.flagged("attempted", offset_ms=10)  # same credential name, a genuine event
    proc.process_event(real_later)
    assert proc.store.get(real_later["event_id"], "slack-sec")["status"] == "delivered"


def test_flush_stops_at_deadline():
    clock = Clock()
    proc = make_processor(make_config(telegram=None, discord=None), clock=clock)
    proc.process_event(fixtures.canary_revoked())
    clock.t += 120
    proc.monotonic = lambda: 100.0
    assert proc.flush_windows(deadline=105.0) == []      # no time: nothing claimed
    assert [o.status for o in proc.flush_windows()] == ["delivered"]  # next flush sends it
