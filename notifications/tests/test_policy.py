"""Requirements 6-11: severity mapping and honest wording per event type."""
import pytest

from burn_notify import fixtures
from burn_notify.events import parse
from burn_notify.policy import (CONTAINMENT_UPDATE, Severity, apply_overrides, classify)


def c(event):
    return classify(parse(event))


@pytest.mark.parametrize("name,category,severity", [
    ("rotation_failed", "containment_failed", Severity.CRITICAL),
    ("rotation_deferred", "containment_partial", Severity.HIGH),
    ("canary_not_contained", "canary_not_contained", Severity.HIGH),
    ("canary_used", "canary_used", Severity.HIGH),
    ("flagged_reported", "flag_reported", Severity.HIGH),
    ("blocked", "disclosure_blocked", Severity.HIGH),
    ("flagged_attempted", "flag_attempted", Severity.MEDIUM),
    ("unregistered", "unregistered_access", Severity.MEDIUM),
    ("burn_revoked", "agent_burned", Severity.MEDIUM),
    ("flagged_seen", "flag_seen", Severity.LOW),
    ("rotated", "credential_rotated", Severity.LOW),
    ("replaced", "instance_replaced", Severity.LOW),
    ("resumed", "task_resumed", Severity.LOW),
    ("refused", "access_refused", Severity.LOW),
    ("dismissed", "flag_dismissed", Severity.LOW),
    ("recorded", "activity", Severity.INFO),
])
def test_severity_mapping(name, category, severity):
    result = c(fixtures.ALL[name]())
    assert (result.category, result.severity) == (category, severity)


def test_canary_used_does_not_claim_containment():
    result = c(fixtures.canary_used())
    text = " ".join([result.title, result.summary] + [v for _, v in result.facts]).lower()
    assert result.priority
    assert dict(result.facts)["Containment"] == "Awaiting verified outcome"
    assert dict(result.facts)["Credential action"] == "Not yet confirmed"
    assert "contained" not in text and "rotated" not in text and "success" not in text


@pytest.mark.parametrize("origin", ["own_session", "burn_session"])
def test_canary_used_inside_burn_is_alert_only(origin):
    result = c(fixtures.canary_used(origin))
    assert result.category == "canary_used_in_burn" and result.severity == Severity.MEDIUM
    assert dict(result.facts)["Containment"] == "Not requested (alert only)"


def test_canary_origin_is_not_read_from_untrusted_sources():
    event = fixtures.canary_used("own_session")
    event["source"] = "gateway"  # detail from a non-Burn writer is never used for classification
    assert c(event).category == "canary_used"


def test_blocked_is_not_called_exfiltration():
    result = c(fixtures.blocked())
    assert "not evidence that data left" in result.summary
    assert dict(result.facts)["Outcome"] == "Blocked"


def test_blocked_without_credential_is_policy_block():
    event = fixtures.blocked()
    event.update(secret_ref=None, fingerprint=None, secret_type=None, owner=None)
    assert c(event).category == "egress_blocked"


def test_flag_escalation_levels():
    assert [c(fixtures.flagged(level)).severity for level in ("seen", "attempted", "reported")] == \
        [Severity.LOW, Severity.MEDIUM, Severity.HIGH]
    assert "not evidence of compromise" in c(fixtures.flagged("seen")).summary


def test_successful_burn_and_rotation_reporting():
    burn = c(fixtures.burn_revoked())
    assert burn.kind == CONTAINMENT_UPDATE and dict(burn.facts)["Status"] == "1 session(s) revoked"
    update = c(fixtures.canary_revoked())
    assert update.category == "canary_contained" and update.kind == CONTAINMENT_UPDATE
    assert dict(update.facts)["Other instances"] == "No broad containment requested"
    rotated = c(fixtures.rotated())
    assert "this event covers that credential only" in rotated.summary
    assert "all" not in rotated.summary.lower().split()


@pytest.mark.parametrize("name", ["rotation_failed", "rotation_deferred", "canary_not_contained"])
def test_partial_or_failed_containment_never_called_successful(name):
    result = c(fixtures.ALL[name]())
    text = " ".join([result.title, result.summary] + [v for _, v in result.facts]).lower()
    assert result.priority
    assert "success" not in text and "contained successfully" not in text
    assert any(word in text for word in ("incomplete", "partial", "not performed", "not contained"))


def test_report_only_role():
    result = c(fixtures.canary_not_contained(report_only=True))
    assert result.category == "canary_report_only" and result.severity == Severity.HIGH


def test_overrides_respect_critical_floor():
    failed = c(fixtures.rotation_failed())
    assert apply_overrides(failed, {"containment_failed": Severity.LOW}).severity == Severity.HIGH
    seen = c(fixtures.flagged("seen"))
    assert apply_overrides(seen, {"flag_seen": Severity.MEDIUM}).severity == Severity.MEDIUM


def test_burn_revoked_count_comes_only_from_template():
    event = fixtures.burn_revoked()
    event["detail"] = "<!channel> everyone look"
    assert dict(c(event).facts)["Status"] == "Badge revoked"
