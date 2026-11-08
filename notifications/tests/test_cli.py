"""The dry run never contacts a platform and prints only redacted previews."""
import io
import pytest
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


def test_live_check_verifies_server_copies_where_providers_return_them(tmp_path):
    report = tmp_path / "r.json"
    code, out = run(["live-check", "--dry-run", "--report", str(report)])
    assert code == 0
    first = json.loads(report.read_text())["steps"][0]["results"]
    checks = {r["provider"]: r["server_copy_check"] for r in first}
    assert checks["telegram"] == "pass" and checks["discord"] == "pass"
    assert checks["slack"].startswith("n/a") and checks["teams"].startswith("n/a")
    assert all(r["at"] for r in first)


def test_server_copy_containing_a_secret_is_flagged():
    from burn_notify import fixtures, incident as inc_mod, verify
    from burn_notify.events import parse
    from burn_notify.policy import classify
    event = parse(fixtures.canary_used())
    inc = inc_mod.build(event, classify(event), dashboard_url=None, notice="SYNTHETIC TEST")
    echo = f"[TEST] {inc.event_id} {inc.agent_id} {inc.severity.label} {inc.ts} leaked-token-123"
    assert "a destination secret appears in the payload" in verify.check_payload(
        inc, None, echo, secrets=("leaked-token-123",))


def test_env_file_loading_rules(tmp_path, monkeypatch):
    env = tmp_path / "test.env"
    env.write_text('# comment\nexport BURN_NOTIFY_SLACK_WEBHOOK="https://hooks.slack.com/services/a/b/c"\nX=1\n')
    env.chmod(0o644)
    with pytest.raises(cli.EnvFileError, match="readable by others"):
        cli.load_env_file(str(env))
    env.chmod(0o600)
    monkeypatch.delenv("BURN_NOTIFY_SLACK_WEBHOOK", raising=False)
    assert cli.load_env_file(str(env)) == ["BURN_NOTIFY_SLACK_WEBHOOK", "X"]
    import os
    assert os.environ["BURN_NOTIFY_SLACK_WEBHOOK"] == "https://hooks.slack.com/services/a/b/c"
    inside = Path(cli.REPO_ROOT) / "notifications" / "config.example.json"
    with pytest.raises(cli.EnvFileError, match="inside the repository"):
        cli.load_env_file(str(inside))


def test_env_file_output_never_shows_values(tmp_path):
    env = tmp_path / "t.env"
    env.write_text("BURN_NOTIFY_TEAMS_WORKFLOW_URL=topsecretvalue\n")
    env.chmod(0o600)
    code, out = run(["live-check", "--dry-run", "--env-file", str(env)])
    assert code == 0 and "topsecretvalue" not in out and "BURN_NOTIFY_TEAMS_WORKFLOW_URL" in out


def test_live_check_without_email_needs_no_aws(monkeypatch):
    """Regression: the first real run crashed creating an SES client with no region configured."""
    from burn_notify import config as config_mod, livecheck
    from burn_notify.http import Response
    for var in ("AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    cfg = config_mod.parse({"destinations": [{"id": "d", "provider": "discord", "secret_ref": "env:D",
                                              "options": {"private_channel_confirmed": True}}]}, environ={})
    from conftest import FakeTransport, DictSecrets
    from burn_notify.providers import REGISTRY
    providers = {n: c(transport=FakeTransport(default=Response(200, {}, b'{"id":"9"}'))) for n, c in REGISTRY.items()
                 if n != "email"}
    providers["email"] = REGISTRY["email"]()  # real provider, no client created unless email sends
    report = livecheck.run(cfg, secrets=DictSecrets({"env:D": "https://discord.com/api/webhooks/1/x"}),
                           providers=providers)
    assert report["duplicate_sent_nothing"]
