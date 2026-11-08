import json
import pytest

import layers
from destinations import audit
from proxy.policy import Settings
from payloads.forms import FORMS
from payloads import heldout
import catch_rate
import cache_costs
from loadtest import Run, outcomes, revocation_report
from live.gateway import summarize_ms


def evidence(records):
    return {"source_commit": "abcdef0", "measured_at": "2026-11-07T23:00:00Z", "records": records}


@pytest.mark.parametrize("change", [{"session_active": "true"}, {"api_rotations": True},
                                  {"outcome": "private-message"}, {"token": "private-value"}])
def test_policy_checks_reject_unprojected_or_malformed_observations(change):
    data = evidence([{"event_id": "one", "used_at": "2026-11-07T23:00:00Z"}])
    case = dict(provider_rejected=True, session_active=True, old_api_key_accepted=True,
                new_incidents=0, api_rotations=0, canary_events=1, outcome="not_dispatched")
    case.update(change)
    data["policy_checks"] = {"own_session": case}
    with pytest.raises(ValueError):
        layers.measure("canaries", data)


def test_missing_evidence_is_unmeasured(tmp_path):
    assert all(v["status"] == "unmeasured" for v in layers.load(tmp_path).values())


def test_local_probe_scope_and_product_provenance_survive_aggregation():
    data = evidence([{"event_id": "one", "recognized": 1, "in_context": 1}])
    data.update(scope="local-product-code", notes="Storage and enrollment are simulated.",
                source_dirty=True, product_source_sha256="a"*64)
    result = layers.measure("references", data)
    assert result["scope"] == data["scope"] and result["notes"] == data["notes"]
    assert result["source_dirty"] and result["product_source_sha256"] == "a"*64
    assert not result["target_met"]


def test_reference_fraction_and_empty_denominator():
    result = layers.measure("references", evidence([
        {"event_id": "one", "recognized": 3, "in_context": 1},
        {"event_id": "two", "recognized": 1, "in_context": 0}]))
    assert result["exposure_rate"] == .25 and not result["target_met"]
    zero = layers.measure("references", evidence([{"event_id": "zero", "recognized": 0, "in_context": 0}]))
    assert zero["exposure_rate"] is None and zero["target_met"] is None


@pytest.mark.parametrize("recognized,exposed", [(-1, 0), (1, 2), (True, 0), (1, -1)])
def test_reference_counts_reject_bad_evidence(recognized, exposed):
    with pytest.raises(ValueError):
        layers.measure("references", evidence([{"event_id": "one", "recognized": recognized, "in_context": exposed}]))


def test_canary_detection_is_distinct_from_completed_burn():
    records = [
        {"event_id": "one", "used_at": "2026-11-07T23:00:00Z", "detected_at": "2026-11-07T23:00:01Z", "burned_at": "2026-11-07T23:00:03Z"},
        {"event_id": "two", "used_at": "2026-11-07T23:00:00Z", "detected_at": "2026-11-07T23:00:01Z"},
        {"event_id": "three", "used_at": "2026-11-07T23:00:00Z"}]
    result = layers.measure("canaries", evidence(records))
    assert result["detection_rate"] == 2/3 and result["burned"] == 1
    assert result["burn_seconds_p95"] == 3
    records.append(records[0])
    with pytest.raises(ValueError, match="unique"):
        layers.measure("canaries", evidence(records))


def test_canary_revocation_does_not_hide_failed_key_rotation():
    data = evidence([{"event_id": "one", "used_at": "2026-11-07T23:00:00Z",
                      "detected_at": "2026-11-07T23:00:01Z", "burned_at": "2026-11-07T23:00:02Z"}])
    data["checks"] = {"provider_rejections": 1, "control_session_active": True,
                      "containment_outcomes": {"contained_rotation_failed": 1}}
    result = layers.measure("canaries", data)
    assert result["detection_rate"] == 1 and result["burned"] == 1
    assert not result["containment_target_met"]
    assert result["checks"]["containment_outcomes"] == {"contained_rotation_failed": 1}


def test_replay_and_injected_failure_are_separate_from_positive_detection_rate():
    data = evidence([{"event_id": "one", "used_at": "2026-11-07T23:00:00Z",
                      "detected_at": "2026-11-07T23:00:01Z", "burned_at": "2026-11-07T23:00:02Z"}])
    data["negative_checks"] = {
        "duplicate_replay": dict(presentations=2, provider_rejections=2, new_incidents=0,
                                 extra_api_rotations=0, extra_canary_events=0),
        "rotation_failure": dict(provider_rejected=True, session_revoked=True, old_api_key_accepted=True,
                                 incomplete_containment_flagged=True, outcome="contained_rotation_failed", rotated_credentials=0)}
    result = layers.measure("canaries", data)
    assert result["uses"] == result["detected"] == 1
    assert result["negative_checks"]["rotation_failure"]["old_api_key_accepted"]
    assert result["negative_checks"]["duplicate_replay"]["new_incidents"] == 0


def test_canary_invalid_timestamps_are_rejected():
    with pytest.raises(ValueError, match="out of order"):
        layers.measure("canaries", evidence([{"event_id": "one", "used_at": "2026-11-07T23:00:02Z",
                                               "detected_at": "2026-11-07T23:00:01Z"}]))


def test_destination_audit_does_not_hide_sink_or_unknown_access_controls():
    result = audit(Settings(gateway_url="https://gateway.example.test/mcp"))
    assert result["outsider_readable"] == 1
    assert result["unverified"] >= 1 and not result["target_met"]
    assert "local proxy configuration" in result["scope"]


def test_throttling_is_not_success_or_containment():
    run = Run()
    run.revoked_at, run.revoked = 1, {0}
    run.calls_log = [(0, 0, False, False, 200), (0, 2, True, True, 200),
                     (0, 3, False, True, 429), (0, 4, False, False, 200),
                     (1, 5, False, True, 503), (1, 6, False, False, 200)]
    report = revocation_report(run)
    assert report["revoked_outcomes"] == dict(refused=1, throttled=1, succeeded=1, other_errors=0)
    assert report["other_outcomes"] == dict(refused=0, throttled=0, succeeded=1, other_errors=1)
    assert sum(outcomes(run.calls_log).values()) == len(run.calls_log)
    assert summarize_ms([])["p95_ms"] is None


def test_heldout_forms_are_separate_and_measure_every_cell():
    assert not {f.name for f in FORMS} & {f.name for f in heldout.FORMS}
    result = catch_rate.run(1, heldout.FORMS)
    assert result["total_payload_runs"] == len(heldout.FORMS) * 4
    assert set(result["per_form"]) == {f.name for f in heldout.FORMS}


def test_report_cli_keeps_public_results_unchanged(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    originals = {p: p.read_bytes() for p in [root/"RESULTS.md", root/"results.json"]}
    subprocess.run([sys.executable, str(root/"run_offline.py"), "--trials", "1", "--evidence-dir", str(tmp_path),
                    "--output-dir", str(tmp_path/"report")], check=True)
    report = json.loads((tmp_path/"report/results.json").read_text())
    assert report["layers"]["canaries"]["status"] == "unmeasured"
    assert all(p.read_bytes() == data for p, data in originals.items())
    text = (tmp_path/"report/RESULTS.md").read_text()
    assert "held-out corpus" in text and "unmeasured" in text


def test_post_cache_cost_uses_observed_read_units_and_all_sizes(tmp_path):
    assert cache_costs.load(tmp_path)["status"] == "unmeasured"
    data = {"source_commit": "abcdef0", "measured_at": "2026-11-07T23:00:00Z", "cache_enabled": True,
            "samples": [{"calls": 100, "read_units": 200, "output_bytes": n} for n in (36, 1024, 16384)]}
    path = tmp_path/"fingerprint_cache.json"
    path.write_text(json.dumps(data))
    report = cache_costs.load(tmp_path)
    assert all(r["read_units_per_call"] == 2 for r in report["samples"])
    assert all(r["dynamodb_read_usd_per_1m_calls"] == .25 for r in report["samples"])
    data["samples"].pop()
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="one measured window"):
        cache_costs.load(tmp_path)


@pytest.mark.parametrize("units", [-1, float("nan"), float("inf"), True])
def test_invalid_cache_units_cannot_be_reported_as_measurements(tmp_path, units):
    data = {"source_commit": "abcdef0", "measured_at": "2026-11-07T23:00:00Z", "cache_enabled": True,
            "samples": [{"calls": 10, "read_units": units, "output_bytes": 36}]}
    (tmp_path/"fingerprint_cache.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        cache_costs.load(tmp_path)
