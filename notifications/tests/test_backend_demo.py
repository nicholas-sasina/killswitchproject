"""Burn's real containment code (via burn_api/scripts/canary_harness.py, on moto) -> notifications.

Guards the coupling between the notification policy and the exact events Burn's code writes:
if a writer changes shape or wording, these fail.
"""
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "demo" / "backend_demo.py"


@pytest.fixture(scope="module")
def demo():
    spec = importlib.util.spec_from_file_location("backend_demo", DEMO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_network():  # the harness only talks to moto in-process; keep the default guard off for imports
    yield


def run(demo, *args):
    out = io.StringIO()
    with redirect_stdout(out):
        assert demo.main(list(args)) == 0
    return out.getvalue()


def test_real_containment_produces_alert_update_and_summary(demo):
    events, facts = demo.burn_events("contained", 3)
    assert facts["outcome"] == "contained" and facts["victim_session"] == "revoked"
    assert [e["action"] for e in events][:2] == ["canary_used", "revoked"]
    text = run(demo, "--workers", "3")
    assert "canary_used              delivered to: slack-security" in text
    assert "canary_contained" in text
    assert "Owned credentials: Rotated where confirmed (2 rotation event(s) so far)" in text
    assert "['skipped_delivered']" in text


def test_real_rotation_failure_is_reported_critical(demo):
    text = run(demo, "--scenario", "rotation-failure", "--workers", "3")
    assert "CRITICAL containment_failed" in text
    assert "Owned credentials: NOT rotated (rotation failed) - action required" in text
    assert "Rotated where confirmed" not in text


def test_demo_refuses_live_without_switches(demo, monkeypatch):
    monkeypatch.delenv("BURN_NOTIFY_LIVE", raising=False)
    with pytest.raises(SystemExit):
        demo.main(["--live", "--workers", "2"])
