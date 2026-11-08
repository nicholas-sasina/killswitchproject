from burn_notify import fixtures, incident as inc_mod, verify
from burn_notify.events import parse
from burn_notify.policy import classify


def _inc(notice=None):
    event = parse(fixtures.blocked())
    return event, inc_mod.build(event, classify(event), dashboard_url="https://burn.example.com/#/alerts",
                                notice=notice)


def test_passes_on_complete_safe_payload():
    event, inc = _inc("SYNTHETIC TEST")
    body = {"text": " ".join(["[TEST]", inc.event_id, inc.agent_id, inc.severity.label, inc.ts, inc.dashboard_url])}
    assert verify.check_payload(inc, event, body) == []


def test_flags_missing_and_leaked():
    event, inc = _inc("SYNTHETIC TEST")
    body = {"text": f"{inc.agent_id} {event['destination']} secret-xyz"}
    problems = verify.check_payload(inc, event, body, secrets=("secret-xyz",))
    assert "missing event id" in problems and "missing dashboard link" in problems
    assert "missing test label" in problems
    assert "event field 'destination' leaked" in problems
    assert "a destination secret appears in the payload" in problems


def test_discord_escapes_do_not_hide_values():
    event, inc = _inc()
    body = {"text": " ".join([inc.event_id, inc.agent_id.replace("-", "\\-"), inc.severity.label, inc.ts,
                              inc.dashboard_url])}
    assert verify.check_payload(inc, event, body) == []
