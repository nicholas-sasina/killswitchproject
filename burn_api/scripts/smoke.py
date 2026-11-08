#!/usr/bin/env python3
"""Live check of the deployed Burn API: session -> events -> burn -> badge revoked -> blast radius.

Reads BURN_API_URL from the environment or the repo-root .env (see make_env.py) and calls the API as the
automation person (the demo token until the stack requires sign-in). Exits 0 with a SKIP message when the API or the
scenario is not deployed, and 1 when it cannot sign in or the API refuses the sign-in.
Usage: python burn_api/scripts/smoke.py [agent_id]   (default: first agent in the scenario)
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "contracts" / "python"))
from burn_contracts.automation import AutomationAuthError, automation_headers  # noqa: E402


def load_env() -> dict:
    env = dict(os.environ)
    path = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                env.setdefault(key, value)
    return env


def person_auth(env: dict):
    """The headers for each call, as the automation person, with the settings read from .env (see load_env).
    The token is minted once and kept in memory."""
    return lambda: automation_headers(env=env)


def signed_in(env: dict):
    """person_auth(env), or None (with a FAIL message) when it cannot sign in. The API is configured, so failing to
    sign in to it is an error, not a reason to skip."""
    auth = person_auth(env)
    try:
        auth()
    except AutomationAuthError as exc:
        print(f"FAIL: cannot sign in to the Burn API ({exc})")
        return None
    return auth


def refused(status: int) -> bool:
    """A 401 or 403: the API is there but did not accept the sign-in. Reported as a failure, never a skip."""
    if status in (401, 403):
        print(f"FAIL: the Burn API refused the sign-in (HTTP {status}); after a sign-in switch, run `make env`")
        return True
    return False


def request(base, auth, method, path, body=None):
    req = urllib.request.Request(
        base + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={**auth(), "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def main() -> int:
    env = load_env()
    base = env.get("BURN_API_URL", "")
    if not base:
        print("SKIP: BURN_API_URL not set (run burn_api/scripts/make_env.py)")
        return 0
    auth = signed_in(env)
    if auth is None:
        return 1
    status, feed = request(base, auth, "GET", "/events?limit=1")
    if refused(status):
        return 1
    if status != 200 or not feed["agents"]:
        print(f"SKIP: API not ready (status {status}) or no scenario loaded")
        return 0
    agent_id = sys.argv[1] if len(sys.argv) > 1 else feed["agents"][0]["agent_id"]

    status, session = request(base, auth, "POST", "/sessions", {"agent_id": agent_id})
    assert status == 201, f"POST /sessions -> {status} {session}"
    sid = session["session_id"]
    print(f"session {sid} started with {len(session['credentials'])} credentials")
    assert request(base, auth, "GET", f"/badge/{sid}")[1]["status"] == "active"

    _, feed = request(base, auth, "GET", "/events")
    assert any(e["action"] == "issued" and e.get("session_id") == sid for e in feed["events"]), "no issued event"

    status, burned = request(base, auth, "POST", "/burn", {"agent_id": agent_id, "reason": "smoke test"})
    assert status == 200 and sid in burned["sessions_revoked"], f"POST /burn -> {status} {burned}"
    print(f"burned: rotated {len(burned['rotated'])}, flagged {len(burned['flagged'])}")
    assert request(base, auth, "GET", f"/badge/{sid}")[1]["status"] == "revoked"

    status, blast = request(base, auth, "GET", f"/burn/{agent_id}/blast-radius")
    assert status == 200, f"blast-radius -> {status} {blast}"
    print(f"blast radius: {len(blast['events'])} CloudTrail events ({blast['note']})")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
