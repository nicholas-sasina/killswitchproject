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
