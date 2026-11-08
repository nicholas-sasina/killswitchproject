import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import burn


def joined(fake, uses=1):
    fake.roles["worker"] = {"name": "worker", "tools": ["notify"], "credentials": ["api_key"], "managed_by": "code"}
    return fake.handle("POST", "/roles/worker/tokens", {}, {"uses": uses})[1]["token"]


def test_session_joins_retries_and_exits_cleanly(fake, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setenv("BURN_JOIN_TOKEN", joined(fake))
    fake.fail[("POST", "/sessions")] = [429]
    with burn.session("worker") as me:
        assert me.agent_id.startswith("agent-worker-") and me.session_id in fake.instances
        assert fake.instances[me.session_id]["status"] == "active"
    assert fake.instances[me.session_id]["status"] == "exited"
    assert len(fake.calls("POST", "/sessions")) == 2


def test_session_exits_even_when_the_body_raises(fake, monkeypatch):
    with pytest.raises(RuntimeError):
        with burn.session("worker", token=joined(fake)) as me:
            raise RuntimeError("boom")
    assert fake.instances[me.session_id]["status"] == "exited"


def test_session_needs_a_token(fake, monkeypatch):
    monkeypatch.delenv("BURN_JOIN_TOKEN", raising=False)
    with pytest.raises(burn.BurnError, match="join token"):
        with burn.session("worker"):
            pass


def test_session_with_a_bad_token_is_a_clear_error(fake):
    joined(fake)
    with pytest.raises(burn.BurnError, match="HTTP 403"):
        with burn.session("worker", token="bjt_" + "0" * 16 + "." + "A" * 43):
            pass


class SpawnStub(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        SpawnStub.seen.append(body)
        status, out = (403, {"error": "role not allowed"}) if body["role"] == "no" else (200, {"session_id": "sess_c", "agent_id": "agent-w-1"})
        data = json.dumps(out).encode()
        self.send_response(status)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_spawn_posts_role_and_argv_to_the_spawn_url(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), SpawnStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("BURN_SPAWN_URL", f"http://127.0.0.1:{server.server_port}/spawn")
    try:
        assert burn.spawn("worker", "python task.py --n 2") == {"session_id": "sess_c", "agent_id": "agent-w-1"}
        assert SpawnStub.seen[-1] == {"role": "worker", "command": ["python", "task.py", "--n", "2"]}
        burn.spawn("worker", ["echo", "a b"])
        assert SpawnStub.seen[-1]["command"] == ["echo", "a b"]
        with pytest.raises(burn.BurnError, match="403: role not allowed"):
            burn.spawn("no", ["x"])
    finally:
        server.shutdown()


def test_spawn_outside_burn_run_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("BURN_SPAWN_URL", raising=False)
    with pytest.raises(burn.BurnError, match="inside `burn run`"):
        burn.spawn("worker", ["x"])
