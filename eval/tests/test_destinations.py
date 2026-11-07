from destinations import audit
from proxy.policy import AWS_HOSTS, Settings


def test_aws_hostnames_are_not_blanket_certified_as_safe():
    report = audit(Settings(sink_url=""))
    assert report["outsider_readable"] == 0
    assert report["unverified"] == len(AWS_HOSTS)
    assert report["zero_readable_destinations"] is None
    assert not report["target_met"]


def test_demo_sink_is_explicitly_counted_and_can_be_disabled():
    demo = audit(Settings())
    restricted = audit(Settings(sink_url=""))
    assert demo["outsider_readable"] == 1
    assert not any(row["host"] == "sink" for row in restricted["destinations"])
    assert Settings(sink_url="").route_reason("sink", 8081) == "destination_not_allowed"


def test_deny_all_session_is_zero_destinations_but_not_a_usable_success():
    report = audit(Settings(), {"egress": []})
    assert report["mode"] == "deny-all"
    assert report["destinations"] == [] and report["zero_readable_destinations"] is True
    assert report["usable_configuration"] is False
    assert report["target_met"] is False


def test_role_audit_uses_only_effectively_allowed_destinations():
    report = audit(Settings(), {"egress": ["s3.amazonaws.com", "outsider.example.test"]})
    assert [row["host"] for row in report["destinations"]] == ["s3.amazonaws.com"]
    assert report["unverified"] == 1 and report["outsider_readable"] == 0
    assert report["zero_readable_destinations"] is None


def test_invalid_role_policy_is_not_a_certified_configuration():
    report = audit(Settings(), {"egress": "sink"})
    assert report["policy_valid"] is False
    assert report["usable_configuration"] is False and report["target_met"] is False


def test_null_and_legacy_session_policy_match_global_audit():
    assert audit(Settings(), {})["destinations"] == audit(Settings(), {"egress": None})["destinations"]
    assert audit(Settings(), {})["destinations"] == audit(Settings())["destinations"]
