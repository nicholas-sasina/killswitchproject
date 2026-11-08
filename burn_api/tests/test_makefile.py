"""Operator command checks use a fake uv executable and never contact AWS or the proxy."""
import json
import os
from pathlib import Path
import secrets
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def command_env(tmp_path):
    executable = tmp_path / "uv"
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ["EXIT_CHECK_CAPTURE"]).write_text(json.dumps({
    "args": sys.argv[1:], "certificate": os.environ.get("BURN_PROXY_CA_CERT"), "cwd": os.getcwd(),
}))
if os.environ.get("EXIT_CHECK_FAIL"):
    print("Exit check unavailable.", file=sys.stderr)
    sys.exit(7)
print(json.dumps({"blocked": os.environ["EXIT_CHECK_BLOCKED"] == "true",
    "reason": os.environ.get("EXIT_CHECK_REASON")}))
''')
    executable.chmod(0o755)
    return {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "EXIT_CHECK_CAPTURE": str(tmp_path / "arguments.json"), "EXIT_CHECK_BLOCKED": "true"}


@pytest.mark.parametrize("blocked,reason,encoding,expected", [
    (True, "credential_detected", "base64", "blocked: credential_detected"),
    (False, None, None, "allowed: no block reason returned"),
])
def test_exit_check_runs_cli_once(command_env, blocked, reason, encoding, expected):
    agent = "agent-" + secrets.token_hex(8)
    command_env["EXIT_CHECK_BLOCKED"] = str(blocked).lower()
    if reason:
        command_env["EXIT_CHECK_REASON"] = reason
    args = ["make", "--no-print-directory", "exit-check-test", f"AGENT={agent}"]
    if encoding:
        args.append(f"ENCODING={encoding}")
    result = subprocess.run(args, cwd=REPO, env=command_env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
    captured = json.loads(Path(command_env["EXIT_CHECK_CAPTURE"]).read_text())
    assert captured == {
        "args": ["run", "--python", "3.12", "--env-file", "../.env", "--extra", "leaktest",
                 "python", "-m", "leaktest", "--agent", agent, "--encoding", encoding or "random"],
        "certificate": str(REPO / ".run" / "proxy-certs" / "mitmproxy-ca-cert.pem"),
        "cwd": str(REPO / "detection"),
    }


def test_exit_check_requires_agent_before_running_cli(command_env):
    result = subprocess.run(["make", "--no-print-directory", "exit-check-test", "AGENT="],
                            cwd=REPO, env=command_env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "usage: make exit-check-test" in result.stdout
    assert not Path(command_env["EXIT_CHECK_CAPTURE"]).exists()


def test_exit_check_preserves_cli_failure(command_env):
    command_env["EXIT_CHECK_FAIL"] = "true"
    result = subprocess.run(["make", "--no-print-directory", "exit-check-test", "AGENT=agent-" + secrets.token_hex(8)],
                            cwd=REPO, env=command_env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Exit check unavailable." in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


# --- hosting: the website's dashboard link, and the dashboard's checks before any deploy ----------
# The real Makefile runs from a copy with empty component folders and fake uv, npm, npx and aws.

FAKE_TOOL = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
tool, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"tool": tool, "args": args, "cwd": Path.cwd().name,
                          "VITE_DASHBOARD_URL": os.environ.get("VITE_DASHBOARD_URL")}) + "\\n")
if tool == "uv" and "scripts/hosted_dashboard.py" in args:
    if args[-1] == "url":
        print(os.environ.get("FAKE_HOSTED_URL", ""))
    else:
        sys.exit(int(os.environ.get("FAKE_CHECK_EXIT", "0")))
if tool == "aws":
    print("https://dfake.cloudfront.net")
'''


@pytest.fixture
def hosting(tmp_path):
    bin_dir, repo = tmp_path / "bin", tmp_path / "repo"
    bin_dir.mkdir()
    for tool in ("uv", "npm", "npx", "aws"):
        (bin_dir / tool).write_text(FAKE_TOOL)
        (bin_dir / tool).chmod(0o755)
    for folder in ("site", "burn_api", "infra"):
        (repo / folder).mkdir(parents=True)
    (repo / "Makefile").write_text((REPO / "Makefile").read_text())
    env = {k: v for k, v in os.environ.items() if k not in ("VITE_DASHBOARD_URL", "MAKEFLAGS")}
    env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", FAKE_LOG=str(tmp_path / "log.jsonl"))

    def make(*args, **extra_env):
        result = subprocess.run(["make", "--no-print-directory", *args], cwd=repo, env={**env, **extra_env},
                                capture_output=True, text=True)
        log = tmp_path / "log.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        log.unlink(missing_ok=True)
        return result, calls
    return make


def npm_build(calls):
    return next(c for c in calls if c["tool"] == "npm" and c["args"] == ["run", "build"])


def test_site_deploy_links_the_hosted_dashboard_once_it_is_live(hosting):
    result, calls = hosting("site-deploy", FAKE_HOSTED_URL="https://dhosted.cloudfront.net")
    assert result.returncode == 0, result.stderr
    assert npm_build(calls)["VITE_DASHBOARD_URL"] == "https://dhosted.cloudfront.net"
    assert any(c["tool"] == "npx" and "BurnSite" in c["args"] for c in calls)


def test_site_deploy_prefers_an_explicit_dashboard_url(hosting):
    for how in ("environment", "make variable"):
        if how == "environment":
            result, calls = hosting("site-deploy", VITE_DASHBOARD_URL="https://mine.example",
                                    FAKE_HOSTED_URL="https://dhosted.cloudfront.net")
        else:
            result, calls = hosting("site-deploy", "VITE_DASHBOARD_URL=https://mine.example",
                                    FAKE_HOSTED_URL="https://dhosted.cloudfront.net")
        assert result.returncode == 0, result.stderr
        assert npm_build(calls)["VITE_DASHBOARD_URL"] == "https://mine.example", how


def test_site_deploy_without_a_hosted_dashboard_leaves_the_link_unset(hosting):
    result, calls = hosting("site-deploy")
    assert result.returncode == 0, result.stderr
    assert npm_build(calls)["VITE_DASHBOARD_URL"] is None  # not even empty: site/.env files still apply


@pytest.mark.parametrize("target,check,stacks", [
    ("dashboard-deploy", "build", ["BurnDashboard"]),
    ("dashboard-hosted", "check-hosted", ["BurnPeople", "BurnApi"]),
])
def test_a_refused_check_deploys_nothing(hosting, target, check, stacks):
    result, calls = hosting(target, FAKE_CHECK_EXIT="1")
    assert result.returncode != 0
    assert [c["args"][-1] for c in calls if c["tool"] == "uv"] == [check]
    assert not any(c["tool"] == "npx" for c in calls)


def test_dashboard_deploy_uploads_only_the_checked_build(hosting):
    result, calls = hosting("dashboard-deploy")
    assert result.returncode == 0, result.stderr
    [deploy] = [c for c in calls if c["tool"] == "npx"]
    assert deploy["args"][deploy["args"].index("deploy") + 1] == "BurnDashboard"
    assert deploy["args"][-2:] == ["-c", "burn:dashboard_build=checked"]
    assert "dashboard: https://dfake.cloudfront.net/" in result.stdout


def test_dashboard_hosted_keeps_sign_in_on_and_deploys_only_people_and_api(hosting):
    result, calls = hosting("dashboard-hosted")
    assert result.returncode == 0, result.stderr
    [deploy] = [c for c in calls if c["tool"] == "npx"]
    args = deploy["args"]
    assert args[args.index("deploy") + 1:args.index("deploy") + 3] == ["BurnPeople", "BurnApi"]
    assert "--exclusively" in args  # BurnDashboard is not redeployed without its build
    assert " ".join(args).endswith("-c burn:people_auth=on -c burn:dashboard_hosted=on")
