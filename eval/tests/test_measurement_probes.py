"""Probe mechanics plus actual local-handler measurements (no deployed AWS)."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

pytest.importorskip("moto")
from probes.__main__ import run_probe, write_private
import layers

ROOT = Path(__file__).resolve().parents[2]


def test_actual_gateway_probe_records_provenance_and_exposure_without_values(tmp_path):
    result = run_probe("references", ROOT, 2)
    summary = layers.measure("references", result)
    assert len(result["records"]) == 2 and summary["recognized"] == 4
    assert 0 <= summary["in_context"] <= summary["recognized"]
    assert result["scope"] == "local-emulator"
    assert result["source_commit"] == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert re.fullmatch(r"[0-9a-f]{64}", result["product_source_sha256"])
    assert result["product_file_sha256"]["gateway/lambdas/interceptor/interceptor.py"]
    assert result["checks"]["responses"] == 2
    assert 0 <= result["checks"]["withheld_responses"] <= 2
    assert all(set(r) == {"event_id", "recognized", "in_context"} for r in result["records"])
    output = tmp_path/"references.json"
    write_private(output, result)
    assert output.stat().st_mode & 0o777 == 0o600
    text = output.read_text()
    assert not re.search(r"mpk_live_[a-f0-9]{32}", text)
    assert "Bearer " not in text


def test_actual_canary_handlers_when_explicit_source_is_available():
    source = os.getenv("BURN_CANARY_PROBE_SOURCE")
    if not source:
        pytest.skip("Set BURN_CANARY_PROBE_SOURCE to the canary owner's checkout")
    result = run_probe("canaries", Path(source), 2)
    summary = layers.measure("canaries", result)
    assert summary["uses"] == summary["detected"] == summary["burned"] == 2
    assert result["checks"]["control_session_active"]
    assert result["checks"]["provider_rejections"] == 2
    assert result["checks"]["rotated_old_api_key_rejections"] == 2
    assert result["checks"]["containment_outcomes"] == {"contained": 2}
    assert result["scope"] == "local-emulator"
    assert "synchronous" in result["notes"]
    assert not re.search(r"mpk_live_[a-f0-9]{32}", json.dumps(result))


def test_actual_canary_negative_checks_keep_replay_and_rotation_failure_visible():
    source = os.getenv("BURN_CANARY_PROBE_SOURCE")
    if not source:
        pytest.skip("Set BURN_CANARY_PROBE_SOURCE to the canary owner's checkout")
    result = run_probe("canaries", Path(source), 2, negative_checks=True)
    summary = layers.measure("canaries", result)
    assert summary["uses"] == 2 and result["checks"]["containment_outcomes"] == {"contained": 2}
    replay = result["negative_checks"]["duplicate_replay"]
    assert replay == dict(presentations=2, provider_rejections=2, new_incidents=0,
                          extra_api_rotations=0, extra_canary_events=0)
    failure = result["negative_checks"]["rotation_failure"]
    assert failure == dict(provider_rejected=True, session_revoked=True, outcome="contained_rotation_failed",
                           rotated_credentials=0, old_api_key_accepted=True, incomplete_containment_flagged=True)
    assert result["checks"]["control_session_active"]
    assert "excluded from the ordinary-use" in result["notes"]


def test_reference_probe_rejects_canary_only_negative_mode():
    with pytest.raises(ValueError, match="only for canaries"):
        run_probe("references", ROOT, 1, negative_checks=True)


@pytest.mark.parametrize("kind,trials", [("not-a-probe", 1), ("references", 0), ("canaries", 101)])
def test_invalid_probe_arguments_fail_before_running_product_code(kind, trials):
    with pytest.raises(ValueError):
        run_probe(kind, ROOT, trials)


def test_missing_canary_feature_is_not_a_passing_measurement(tmp_path):
    with pytest.raises(RuntimeError, match="did not|failed"):
        run_probe("canaries", tmp_path, 1)


def test_source_network_attempt_is_blocked_without_relaying_private_output(tmp_path):
    harness = tmp_path/"burn_api/scripts/canary_harness.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("import socket\nsocket.socket().connect(('127.0.0.1', 1))\n")
    result = subprocess.run([sys.executable, "-m", "probes.worker", "canaries", "--source-root", str(tmp_path), "--trials", "1"],
                            cwd=ROOT/"eval", capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "Offline product probe failed", "error_type": "RuntimeError"}
