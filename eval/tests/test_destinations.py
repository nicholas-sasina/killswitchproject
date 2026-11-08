from destinations import audit
from proxy.policy import AWS_HOSTS, Settings
import json
import os
import pytest
import destinations


@pytest.fixture
def declared(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"gateway_url": "https://gateway.example.test/private-path",
                                   "provider_url": "", "cognito_token_url": "", "sink_url": ""}))
    return settings


def test_declared_audit_round_trip_has_only_hosts_and_configuration_evidence(declared, tmp_path):
    result = destinations.declared_audit(declared)
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(result))
    loaded = destinations.load_audit(path)
    assert loaded == result
    assert "private-path" not in json.dumps(loaded)
    assert loaded["provenance"]["network_requests"] is False
    assert loaded["target_met"] is False


def test_session_projection_and_deny_all_round_trip(declared, tmp_path):
    session = tmp_path / "session.json"
    session.write_text('{"egress": []}')
    result = destinations.declared_audit(declared, session)
    output = tmp_path / "audit.json"
    output.write_text(json.dumps(result))
    loaded = destinations.load_audit(output)
    assert loaded["mode"] == "gateway-only"
    assert [r["host"] for r in loaded["destinations"]] == ["gateway.example.test"]
    assert loaded["unverified"] == 1 and loaded["target_met"] is False
    session.write_text('{"egress": [], "api_key": "private-value"}')
    with pytest.raises(ValueError):
        destinations.declared_audit(declared, session)


@pytest.mark.parametrize("value", ["https://user@example.test", "https://example.test/?token=private",
                                  "https://example.test/#private", "https://example.test:99999"])
def test_private_url_credentials_and_malformed_urls_rejected(declared, value):
    data = json.loads(declared.read_text())
    data["gateway_url"] = value
    declared.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        destinations.declared_audit(declared)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(target_met=True),
    lambda r: r.update(unverified=0),
    lambda r: r.update(extra="private-value"),
    lambda r: r["provenance"].update(network_requests=True),
    lambda r: r["destinations"][0].update(basis="private-value"),
    lambda r: r["destinations"].append(r["destinations"][0]),
])
def test_import_rejects_inconsistent_assertions_or_unprojected_data(declared, tmp_path, mutation):
    result = destinations.declared_audit(declared)
    mutation(result)
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        destinations.load_audit(path)


def test_output_is_private_and_cannot_overwrite_input_or_leave_ignored_tree(declared, tmp_path, monkeypatch):
    monkeypatch.setattr(destinations, "OUT", tmp_path)
    with pytest.raises(ValueError):
        destinations._write_output(declared, {}, (declared,))
    with pytest.raises(ValueError):
        destinations._write_output(tmp_path.parent / "public.json", {})
    output = tmp_path / "nested/audit.json"
    destinations._write_output(output, {"ok": True})
    assert os.stat(output).st_mode & 0o777 == 0o600


def test_cli_errors_do_not_print_private_inputs(declared, capsys):
    declared.write_text('{"private-secret": "sensitive-value"}')
    with pytest.raises(SystemExit):
        destinations.main(["--settings-json", str(declared)])
    output = capsys.readouterr()
    assert "private-secret" not in output.err and "sensitive-value" not in output.err


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
