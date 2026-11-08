"""The dry run never contacts a platform and prints only redacted previews."""
import io
import json
from pathlib import Path

from burn_notify import cli, config as config_mod

from conftest import SLACK_URL

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def run(argv):
    out = io.StringIO()
    return cli.main(argv, out), out.getvalue()


def test_demo_dry_run_shows_canary_then_containment_update(monkeypatch):
    monkeypatch.delenv("BURN_NOTIFY_CONFIG", raising=False)
    code, out = run(["dry-run", "--demo"])
    assert code == 0
    first, second = out.index("Canary credential presented"), out.index("Burn containment update")
    assert first < second
    assert "Containment summary" in out and "Rotated where confirmed" in out
    assert "Dry run: no message left this machine." in out
    assert "203.0.113.9" not in out and "canary_" not in out.split("=== delivery history")[0].replace(
        "canary_used", "").replace("canary_revoked", "").replace("canary_contained", "")


def test_dry_run_with_example_config(monkeypatch):
    for name in ("SLACK", "TELEGRAM", "DISCORD", "TEAMS"):
        monkeypatch.delenv(f"BURN_NOTIFY_{name}", raising=False)
    code, out = run(["dry-run", "--config", str(EXAMPLE), "--fixture", "rotation_failed", "--fixture", "blocked"])
    assert code == 0 and "CRITICAL" in out and "delivered" in out


def test_example_config_is_valid_and_private():
    cfg = config_mod.parse(json.loads(EXAMPLE.read_text()), environ={})
    providers = {d.provider for d in cfg.destinations}
    assert providers == {"slack", "telegram", "discord", "teams", "email"}
    assert all(d.secret_ref is None or d.secret_ref.startswith(("env:", "aws-sm:")) for d in cfg.destinations)
    assert SLACK_URL not in EXAMPLE.read_text()


def test_live_send_refuses_without_both_switches(monkeypatch):
    monkeypatch.delenv("BURN_NOTIFY_LIVE", raising=False)
    code, out = run(["send", "--config", str(EXAMPLE), "--destination", "slack-security", "--live"])
    assert code == 2 and "refusing" in out
    monkeypatch.setenv("BURN_NOTIFY_LIVE", "1")
    code, out = run(["send", "--config", str(EXAMPLE), "--destination", "slack-security"])
    assert code == 2


def test_unknown_fixture(monkeypatch):
    code, out = run(["dry-run", "--fixture", "nope"])
    assert code == 2


def test_live_check_dry_run_full_sequence(tmp_path):
    report = tmp_path / "report.json"
    code, out = run(["live-check", "--dry-run", "--inject-failure", "--report", str(report)])
    assert code == 0 and "overall: PASS" in out and "duplicate sent nothing: True" in out
    data = json.loads(report.read_text())
    steps = {s["step"]: s for s in data["steps"]}
    assert all(v == 0 for v in steps["5 duplicate of the canary alert"]["requests_sent"].values())
    assert {r["status"] for r in steps["6a injected failure (first receive)"]["results"]} == {"retrying"}
    assert {r["status"] for r in steps["6b SQS redelivery"]["results"]} == {"delivered"}
    for secret in ("hooks.slack.com/services", "api.telegram.org/bot", "discord.com/api/webhooks"):
        assert secret not in report.read_text() and secret not in out


def test_live_check_refuses_without_switches(monkeypatch):
    monkeypatch.delenv("BURN_NOTIFY_LIVE", raising=False)
    code, out = run(["live-check", "--config", str(EXAMPLE), "--live"])
    assert code == 2 and "refusing" in out
