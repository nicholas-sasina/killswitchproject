"""CLI tests against a stub Burn API (http.server in a thread)."""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from burn_cli.main import run

TOKEN = secrets.token_hex(12)  # fresh each run; tests assert it never reaches the output
EVENT = {"event_id": "evt_1", "ts": "2026-01-01T12:00:00.123Z", "agent_id": "agent-a", "source": "burn",
         "action": "rotated", "secret_ref": "burn/agent-a/api_key", "detail": "credential rotated"}
OTHER = {**EVENT, "event_id": "evt_2", "agent_id": "agent-b", "action": "issued", "secret_ref": None}
ROUTES = {
    ("GET", "/events"): {"events": [EVENT, OTHER], "cursor": "evt_2",
                         "agents": [{"agent_id": "agent-a", "role": "reader", "status": "active",
                                     "session_id": "sess_1", "exposures": 2}]},
    ("POST", "/burn"): {"agent_id": "agent-a", "revoked": True, "sessions_revoked": ["sess_1"],
                        "rotated": ["burn/agent-a/api_key"], "flagged": ["burn/shared/crm_db_url"]},
    ("GET", "/agents/agent-a/ledger"): {"agent_id": "agent-a", "entries": [{
        "secret_ref": "burn/agent-a/api_key", "fingerprint": "ab12cd34" + "0" * 56, "secret_type": "api_key",
        "owner": "agent", "source": "tool_response", "via": "read_config", "first_seen": "t", "status": "rotated"}]},
    ("GET", "/agents/agent-b/ledger"): {"agent_id": "agent-b", "entries": []},
    ("GET", "/burn/agent-a/blast-radius"): {"agent_id": "agent-a", "keys": [], "events": [],
                                            "note": "CloudTrail event history can lag 5-15 minutes behind real activity."},
    ("GET", "/burn/agent-c/blast-radius"): {"agent_id": "agent-c", "keys": [
        {"key_ref": "burn/agent-c/aws_key#v1", "key_hint": "…ABCD", "status": "inactive"}], "events": [
        {"ts": "2026-01-01T00:00:00Z", "key_ref": "burn/agent-c/aws_key#v1", "event_name": "ListBuckets"}], "note": ""},
}


class Stub(BaseHTTPRequestHandler):
    requests = []

    def _serve(self, method):
        path = self.path.split("?")[0]
        Stub.requests.append((method, self.path, json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")))
        if self.headers.get("x-burn-demo-token") != TOKEN:
            return self._send(401, {"error": "missing or invalid x-burn-demo-token"})
        body = ROUTES.get((method, path))
        self._send(200, body) if body is not None else self._send(404, {"error": "not found: agent-zzz"})

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = lambda self: self._serve("GET")  # noqa: E731
    do_POST = lambda self: self._serve("POST")  # noqa: E731

    def log_message(self, *args):
        pass


@pytest.fixture
def api(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Stub.requests.clear()
    monkeypatch.setenv("BURN_API_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("BURN_DEMO_TOKEN", TOKEN)
    yield server
    server.shutdown()


def burn(capsys, *argv):
    code = run(list(argv))
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    return code, out.out, out.err


def test_agents(api, capsys):
    code, out, _ = burn(capsys, "agents")
    assert code == 0 and "agent-a" in out and "reader" in out and "active" in out


def test_kill_yes_shows_refs(api, capsys):
    code, out, _ = burn(capsys, "kill", "agent-a", "--reason", "test", "--yes")
    assert code == 0 and "burn/agent-a/api_key" in out and "burn/shared/crm_db_url" in out
    assert Stub.requests[-1][2] == {"agent_id": "agent-a", "reason": "test"}


def test_kill_asks_and_aborts(api, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code, out, _ = burn(capsys, "kill", "agent-a")
    assert code == 1 and "aborted" in out and not any(r[0] == "POST" for r in Stub.requests)


def test_kill_confirmed_by_prompt(api, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert burn(capsys, "kill", "agent-a")[0] == 0


def test_ledger_prints_fingerprint_prefix_only(api, capsys):
    code, out, _ = burn(capsys, "ledger", "agent-a")
    assert code == 0 and "ab12cd34" in out and "ab12cd34" + "0" * 8 not in out
    assert "has not seen" in burn(capsys, "ledger", "agent-b")[1]


def test_blast_radius_empty_shows_lag_note(api, capsys):
    code, out, _ = burn(capsys, "blast-radius", "agent-a")
    assert code == 0 and "lag 5-15 minutes" in out


def test_blast_radius_with_events(api, capsys):
    out = burn(capsys, "blast-radius", "agent-c")[1]
    assert "ListBuckets" in out and "inactive" in out


def test_events_filter_since_and_follow(api, capsys):
    out = burn(capsys, "events", "--agent", "agent-a")[1]
    assert "burn/agent-a/api_key" in out and "agent-b" not in out
    burn(capsys, "events", "--since", "evt_0")
    assert Stub.requests[-1][1] == "/events?since=evt_0"
    burn(capsys, "events", "--follow", "--max-polls", "2")
    assert Stub.requests[-1][1] == "/events?since=evt_2"  # second poll continues from the cursor


def test_json_prints_raw_response(api, capsys):
    out = burn(capsys, "--json", "ledger", "agent-a")[1]
    assert json.loads(out) == ROUTES[("GET", "/agents/agent-a/ledger")]


def test_api_error_exits_1(api, capsys):
    code, _, err = burn(capsys, "ledger", "agent-zzz")
    assert code == 1 and "HTTP 404" in err


def test_wrong_token_exits_1(api, capsys, monkeypatch):
    monkeypatch.setenv("BURN_DEMO_TOKEN", "wrong")
    code, _, err = burn(capsys, "agents")
    assert code == 1 and "HTTP 401" in err


def test_unreachable_api_exits_1(capsys, monkeypatch):
    monkeypatch.setenv("BURN_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("BURN_DEMO_TOKEN", TOKEN)
    assert burn(capsys, "agents")[0] == 1


def test_missing_config_exits_2(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("BURN_API_URL", raising=False)
    monkeypatch.delenv("BURN_DEMO_TOKEN", raising=False)
    code = run(["agents"], dotenv=tmp_path / "none.env")
    err = capsys.readouterr().err
    assert code == 2 and "BURN_API_URL not set" in err


def test_config_from_dotenv(capsys, monkeypatch, tmp_path):
    from burn_cli import config
    monkeypatch.delenv("BURN_API_URL", raising=False)
    monkeypatch.delenv("BURN_DEMO_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text('# c\nBURN_API_URL=http://x.invalid/\nBURN_DEMO_TOKEN="t0k"\n')
    assert config.load(env) == ("http://x.invalid", "t0k")


def test_usage_error_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        run(["kill"])
    assert exc.value.code == 2
