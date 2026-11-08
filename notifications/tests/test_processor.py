"""Requirements 12-17, 20, 21 plus aggregation, digest and rate limiting."""
import threading

from burn_notify import fixtures, store as st
from burn_notify.http import Response

from conftest import Clock, FakeTransport, make_config, make_processor


def statuses(proc, event):
    return {r["destination_id"]: r["status"] for r in proc.store.list_deliveries(event["event_id"])}


def test_high_event_routes_by_severity():
    proc = make_processor()
    event = fixtures.canary_used()
    proc.process_event(event)
    # slack/discord min medium, telegram min high -> sent; teams/email min critical -> not routed
    assert statuses(proc, event) == {"slack-sec": "delivered", "tg-oncall": "delivered", "discord-sec": "delivered"}


def test_critical_reaches_every_critical_destination():
    proc = make_processor()
    event = fixtures.rotation_failed()
    proc.process_event(event)
    assert set(statuses(proc, event).values()) == {"delivered"} and len(statuses(proc, event)) == 5


def test_per_destination_severity_filtering():
    proc = make_processor()
    event = fixtures.flagged("attempted")  # medium
    proc.process_event(event)
    assert set(statuses(proc, event)) == {"slack-sec", "discord-sec"}


def test_disabled_destination_never_sends():
    slack = FakeTransport()
    proc = make_processor(make_config(slack={"enabled": False}), transports={"slack": slack})
    event = fixtures.rotation_failed()
    proc.process_event(event)
    assert "slack-sec" not in statuses(proc, event) and slack.calls == []


def test_category_and_agent_filters():
    cfg = make_config(slack={"categories": ["canary_used"]}, discord={"exclude_categories": ["canary_used"]},
                      telegram={"agents": ["agent-other-000000"]})
    proc = make_processor(cfg)
    event = fixtures.canary_used()
    proc.process_event(event)
    assert set(statuses(proc, event)) == {"slack-sec"}


def test_role_filter():
    proc = make_processor(make_config(slack={"roles": ["reader"]}))
    event = fixtures.canary_used()
    proc.process_event(event)
    assert "slack-sec" not in statuses(proc, event)


def test_duplicate_stream_records_deliver_once():
    slack = FakeTransport()
    proc = make_processor(transports={"slack": slack})
    event = fixtures.canary_used()
    record = fixtures.stream_record(event)
    proc.process_record(record)
    second = proc.process_record(record)
    assert len(slack.calls) == 1
    assert {o.status for o in second.outcomes} == {"skipped_delivered"}


def test_concurrent_workers_deliver_once():
    slack = FakeTransport()
    proc = make_processor(transports={"slack": slack})
    event = fixtures.canary_used()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        proc.process_event(dict(event))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(slack.calls) == 1
    assert statuses(proc, event)["slack-sec"] == "delivered"


def test_crashed_worker_lease_expires_and_is_retried():
    clock = Clock()
    proc = make_processor(clock=clock)
    event = fixtures.canary_used()
    # A worker claimed and died mid-send.
    assert proc.store.claim(event["event_id"], "slack-sec", "slack", clock()).won
    first = proc.process_event(event)
    assert next(o for o in first.outcomes if o.destination_id == "slack-sec").status == "skipped_sending"
    clock.t += proc.store.lease_seconds + 1
    proc.process_event(event)
    row = proc.store.get(event["event_id"], "slack-sec")
    assert row["status"] == "delivered" and row["attempts"] == 2


def test_timeout_quick_retry_then_success():
    slack = FakeTransport(TimeoutError(), Response(200, {}, b"ok"))
    proc = make_processor(transports={"slack": slack})
    event = fixtures.canary_used()
    proc.process_event(event)
    assert len(slack.calls) == 2 and statuses(proc, event)["slack-sec"] == "delivered"


def test_connection_failure_twice_goes_to_retrying():
    slack = FakeTransport(ConnectionError(), ConnectionError())
    proc = make_processor(transports={"slack": slack})
    event = fixtures.canary_used()
    result = proc.process_event(event)
    assert result.needs_retry
    row = proc.store.get(event["event_id"], "slack-sec")
    assert row["status"] == "retrying" and row["failure_category"] == "network_error"


def test_http_429_retry_after_is_propagated():
    tg = FakeTransport(Response(429, {}, b'{"ok":false,"error_code":429,"parameters":{"retry_after":30}}'))
    proc = make_processor(transports={"telegram": tg})
    event = fixtures.canary_used()
    result = proc.process_event(event)
    assert result.needs_retry and result.retry_after == 30
    assert len(tg.calls) == 1  # too long for an in-process retry


def test_short_429_is_retried_in_process():
    slack = FakeTransport(Response(429, {"retry-after": "1"}, b""), Response(200, {}, b"ok"))
    proc = make_processor(transports={"slack": slack})
    event = fixtures.canary_used()
    assert not proc.process_event(event).needs_retry and len(slack.calls) == 2


def test_redelivery_only_retries_failed_destinations():
    slack = FakeTransport(ConnectionError(), ConnectionError(), Response(200, {}, b"ok"))
    discord = FakeTransport(default=Response(200, {}, b'{"id":"1"}'))
    proc = make_processor(transports={"slack": slack, "discord": discord})
    event = fixtures.canary_used()
    assert proc.process_event(event).needs_retry
    assert not proc.process_event(event, receive_count=2).needs_retry
    assert len(discord.calls) == 1 and len(slack.calls) == 3
    assert statuses(proc, event)["slack-sec"] == "delivered"


def test_exhausted_retries_mark_permanently_failed():
    slack = FakeTransport(default=Response(503, {}, b""))
    proc = make_processor(transports={"slack": slack}, max_receives=3)
    event = fixtures.canary_used()
    proc.process_event(event, receive_count=3)
    row = proc.store.get(event["event_id"], "slack-sec")
    assert row["status"] == "permanently_failed" and row["failure_category"].startswith("retries_exhausted")


def test_permanent_error_is_not_retried():
    slack = FakeTransport(Response(403, {}, b"invalid_token"))
    proc = make_processor(transports={"slack": slack})
    event = fixtures.canary_used()
    result = proc.process_event(event)
    assert not result.needs_retry and statuses(proc, event)["slack-sec"] == "permanently_failed"
    proc.process_event(event)
    assert len(slack.calls) == 1


def test_missing_secret_is_permanent_and_isolated():
    from conftest import DictSecrets, SECRETS
    secrets = DictSecrets({k: v for k, v in SECRETS.items() if k != "env:SLACK"})
    proc = make_processor(secrets=secrets)
    event = fixtures.canary_used()
    proc.process_event(event)
    rows = statuses(proc, event)
    assert rows["slack-sec"] == "permanently_failed" and rows["tg-oncall"] == "delivered"
    assert proc.store.get(event["event_id"], "slack-sec")["failure_category"] == "secret_unavailable"


def test_provider_outage_is_isolated():
    slack = FakeTransport(default=Response(503, {}, b""))

    class Boom:
        def post(self, *a, **k):
            raise RuntimeError("unexpected bug in adapter")

    proc = make_processor(transports={"slack": slack, "discord": Boom()})
    event = fixtures.canary_used()
    result = proc.process_event(event)
    rows = statuses(proc, event)
    assert rows["tg-oncall"] == "delivered"
    assert rows["slack-sec"] == "retrying" and rows["discord-sec"] == "retrying"
    assert result.needs_retry


def test_delayed_lower_flag_level_is_superseded():
    proc = make_processor()
    reported = fixtures.flagged("reported", offset_ms=10)
    late_attempted = fixtures.flagged("attempted")  # older event, delivered later
    proc.process_event(reported)
    proc.process_event(late_attempted)
    assert statuses(proc, reported)["slack-sec"] == "delivered"
    row = proc.store.get(late_attempted["event_id"], "slack-sec")
    assert row["status"] == "suppressed" and row["failure_category"] == "superseded_by_higher_flag_level"


def test_containment_update_never_overstates_after_late_failure():
    proc = make_processor()
    proc.process_event(fixtures.rotation_failed())
    inc, _ = proc.incident_for(fixtures.canary_revoked())
    assert dict(inc.facts)["Owned credentials"].startswith("NOT rotated")


def test_containment_update_before_rotation_says_not_confirmed():
    proc = make_processor()
    inc, _ = proc.incident_for(fixtures.canary_revoked())
    facts = dict(inc.facts)
    assert facts["Owned credentials"] == "Rotation not yet confirmed"
    assert facts["Status"] == "Session revoked"


def test_containment_summary_follows_with_recorded_results():
    clock = Clock()
    slack = FakeTransport()
    proc = make_processor(transports={"slack": slack}, clock=clock)
    proc.process_event(fixtures.canary_used())
    proc.process_event(fixtures.canary_revoked())
    proc.process_event(fixtures.rotated())
    proc.process_event(fixtures.flagged("seen"))
    clock.t += 120
    proc.flush_windows()
    summaries = [inc for dest, inc in proc.flushed if dest == "slack-sec"]
    assert len(summaries) == 1
    facts = dict(summaries[0].facts)
    assert facts["Owned credentials"].startswith("Rotated where confirmed (1")
    assert facts["Shared credentials"].startswith("Flagged for review (1")
    proc.flush_windows()  # flushing twice never resends
    assert len([d for d, _ in proc.flushed if d == "slack-sec"]) == 1


def test_blocked_storm_is_aggregated_and_summarized():
    clock = Clock()
    slack = FakeTransport()
    proc = make_processor(make_config(telegram=None, discord=None), transports={"slack": slack}, clock=clock)
    events = [fixtures.blocked(offset_ms=i) for i in range(50)]
    for event in events:
        proc.process_event(event)
    assert len(slack.calls) == 3  # individual_per_window
    suppressed = [proc.store.get(e["event_id"], "slack-sec")["status"] for e in events[3:]]
    assert set(suppressed) == {"suppressed"}
    clock.t += 400
    proc.flush_windows()
    assert len(slack.calls) == 4
    summary = proc.flushed[0][1]
    assert summary.aggregated_count == 47
    assert "No successful disclosure has been established" in summary.summary


def test_critical_incidents_are_never_aggregated():
    slack = FakeTransport()
    proc = make_processor(make_config(telegram=None, discord=None, teams=None, email=None),
                          transports={"slack": slack})
    for i in range(10):
        proc.process_event(fixtures.rotation_failed(offset_ms=i))
    assert len(slack.calls) == 10


def test_low_severity_digest():
    clock = Clock()
    slack = FakeTransport()
    cfg = make_config(slack={"min_severity": "medium", "digest_low": True}, telegram=None, discord=None)
    proc = make_processor(cfg, transports={"slack": slack}, clock=clock)
    for i in range(5):
        proc.process_event(fixtures.flagged("seen", ref=f"burn/shared/key_{i}", offset_ms=i))
    assert slack.calls == []
    clock.t += 3700
    proc.flush_windows()
    assert len(slack.calls) == 1
    assert proc.flushed[0][1].aggregated_count == 5 and proc.flushed[0][1].title == "Low-severity digest"


def test_rate_limit_defers_non_critical():
    clock = Clock()
    slack = FakeTransport()
    cfg = make_config({"aggregation": {"individual_per_window": 100}}, slack={"rate_per_minute": 2},
                      telegram=None, discord=None)
    proc = make_processor(cfg, transports={"slack": slack}, clock=clock)
    results = [proc.process_event(fixtures.canary_used(offset_ms=i)) for i in range(3)]
    assert len(slack.calls) == 2
    assert results[2].needs_retry and results[2].outcomes[0].category == "local_rate_limit"


def test_events_with_no_route_record_nothing():
    proc = make_processor()
    event = fixtures.recorded()
    result = proc.process_event(event)
    assert result.outcomes == [] and proc.store.list_deliveries() == []


def test_ledger_is_never_written():
    """The processor has no handle on BURN tables: its only writes go to its own store."""
    import burn_notify.processor as processor_module
    source = open(processor_module.__file__).read()
    assert "burn-events" not in source and "put_item" not in source
