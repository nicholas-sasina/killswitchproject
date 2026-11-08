#!/usr/bin/env python3
"""End-to-end containment check against the deployed system.

    python burn_api/scripts/e2e.py [agent_id]     (a scenario must be loaded; see agents/scripts/load_scenario.py)

session -> badge token -> gateway tool call works -> POST /burn -> the same token's next gateway
call is refused -> the burned agent's pre-rotation API key is rejected by the provider.
Prints secret_refs and status codes only, never a credential value.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

import boto3

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = _load("burn_smoke", HERE / "smoke.py")                      # request(), load_env(), signed_in()
gw = _load("gateway_smoke", REPO / "gateway" / "scripts" / "smoke.py")  # Mcp, get_token()


def main() -> int:
    env = api.load_env()
    base = env.get("BURN_API_URL", "")
    if not base:
        print("SKIP: run burn_api/scripts/make_env.py first")
        return 0
    auth = api.signed_in(env)  # the automation person's headers, minted once
    if auth is None:
        return 1
    status, feed = api.request(base, auth, "GET", "/events?limit=1")
    if api.refused(status):
        return 1
    if not feed.get("agents"):
        print("SKIP: no scenario loaded")
        return 0
    agent_id = sys.argv[1] if len(sys.argv) > 1 else feed["agents"][0]["agent_id"]
    failures = []

    def check(ok, label):
        print(("PASS " if ok else "FAIL ") + label)
        if not ok:
            failures.append(label)

    status, session = api.request(base, auth, "POST", "/sessions", {"agent_id": agent_id})
    assert status == 201, f"POST /sessions -> {status} {session}"
    badge = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId=session["badge_ref"])["SecretString"])
    mcp = gw.Mcp(env["BURN_GATEWAY_URL"], gw.get_token(badge))
    mcp.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "burn-e2e", "version": "1"}})
    mcp.call("notifications/initialized", notify=True)
    tools = [t["name"] for t in mcp.call("tools/list")["result"]["tools"]]
    tool = next((t for t in ("list_tickets", "read_inbox") if t in tools), tools[0])  # tools needing no arguments
    is_error, _ = mcp.tool(tool, {})
    check(not is_error, f"{agent_id}: {tool} works with an active badge")

    status, burned = api.request(base, auth, "POST", "/burn", {"agent_id": agent_id, "reason": "e2e"})
    check(status == 200 and session["session_id"] in burned["sessions_revoked"], "burn revoked the session")
    check(set(burned["rotated"]) >= {c["secret_ref"] for c in session["credentials"]}, f"rotated {burned['rotated']}")
    check(api.request(base, auth, "GET", f"/badge/{session['session_id']}")[1]["status"] == "revoked", "badge revoked")

    is_error, text = mcp.tool(tool, {})
    check(is_error and text.startswith("BURN:"), "same token refused at the gateway after burn")

    # IAM changes take a few seconds to propagate, so retry before calling the old AWS key alive.
    for attempt in range(6):
        replay = subprocess.run(["uv", "run", "python", "scripts/replay_old_key.py", "--agent", agent_id],
                                cwd=REPO / "minter", capture_output=True, text=True)
        # An agent may own only some credential types, so judge by "was anything still accepted".
        if "ACCEPTED" not in replay.stdout:
            break
        time.sleep(10)
    print(replay.stdout.strip())
    check("ACCEPTED" not in replay.stdout, "no pre-rotation credential is still accepted")
    print("E2E OK" if not failures else f"E2E FAILED: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
