import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from unittest.mock import Mock

import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
import httpx

from burn_contracts.events import make_event
from leaktest.app import create_app
from leaktest.service import ENCODINGS, LeakTest
from scripts.proxy_env import write_env
from scripts.prepare_build import prepare
from sink.__main__ import Receipts, handler


def test_sink_records_only_size_and_digest(tmp_path):
    path = tmp_path / "receipts.jsonl"
    receipts = Receipts(path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(receipts))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    value = secrets.token_bytes(32)
    try:
        response = httpx.post(f"http://127.0.0.1:{server.server_port}/webhook", content=value, trust_env=False)
        expected = {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        assert response.json() == expected
        assert json.loads(path.read_text()) == expected
        assert receipts.snapshot() == [expected]
        assert value.hex() not in path.read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_proxy_environment_file_is_private_and_atomic(tmp_path):
    path = tmp_path / ".run" / "proxy.env"
    value = secrets.token_urlsafe(32)
    write_env(path, {"AWS_SECRET_ACCESS_KEY": value})
    assert path.stat().st_mode & 0o777 == 0o600
    write_env(path, {"AWS_SECRET_ACCESS_KEY": secrets.token_urlsafe(32)})
    assert value not in path.read_text()
    with pytest.raises(ValueError):
        write_env(path, {"AWS_SECRET_ACCESS_KEY": value + "\n"})


def test_build_context_excludes_ignored_local_values(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".run/\n.env\n.venv/\n")
    source = repo / "detection"
    source.mkdir()
    (source / "safe.py").write_text("pass\n")
    (source / ".env").write_text(secrets.token_urlsafe(32))
    (source / ".venv").mkdir()
    (source / ".venv" / "local").write_text("ignored")
    target = tmp_path / "context"
    prepare(repo, target)
    assert (target / "detection" / "safe.py").exists()
    assert not (target / "detection" / ".env").exists()
    assert not (target / "detection" / ".venv").exists()


class FakeCloud:
    def __init__(self):
        self.agent = "agent-" + secrets.token_hex(4)
        self.ref = f"burn/{self.agent}/api_key"
        self.value = "mpk_" + "live_" + secrets.token_hex(16)
        self.secret_reads = []

    def Table(self, name):
        if name == "burn-exposures":
            return Mock(query=Mock(return_value={"Items": [{"secret_ref": self.ref}]}))
        return Mock(query=Mock(return_value={"Items": [{"status": "active", "session_id": secrets.token_urlsafe(24)}]}))

    def get_secret_value(self, SecretId):
        self.secret_reads.append(SecretId)
        return {"SecretString": self.value}


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_leak_tool_transports_without_returning_values(encoding):
    cloud = FakeCloud()
    seen = []
    event = make_event(agent_id=cloud.agent, source="egress", action="blocked")
    def transport(request):
        seen.append(request.content)
        return httpx.Response(403, json={"reason": "credential_detected", "event_id": event["event_id"]})
    def client_factory(**kwargs):
        assert cloud.value not in kwargs["proxy"]
        return httpx.Client(transport=httpx.MockTransport(transport))
    service = LeakTest(ddb=cloud, secrets_client=cloud, client_factory=client_factory)
    result = service.run(agent_id=cloud.agent, encoding=encoding)
    assert result["blocked"] is True
    assert result["reason"] == "credential_detected"
    assert result["event_id"] == event["event_id"]
    assert result["secret_ref"] == cloud.ref
    assert result["encoding"] in ENCODINGS[:-1]
    assert result["requests_sent"] == 1
    assert cloud.value not in json.dumps(result)
    assert cloud.secret_reads == [cloud.ref]
    assert seen


def test_split_sends_second_request_and_free_text_is_unchanged():
    cloud = FakeCloud()
    seen = []
    def transport(request):
        seen.append(request.content)
        return httpx.Response(200, json={"ok": True})
    service = LeakTest(ddb=cloud, secrets_client=cloud,
        client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(transport)))
    result = service.run(agent_id=cloud.agent, encoding="split_across_requests")
    assert result["requests_sent"] == 2 and not result["blocked"]
    assert b"".join(seen) == cloud.value.encode()
    seen.clear()
    text = secrets.token_hex(16)
    result = service.run(agent_id=cloud.agent, encoding="hex", free_text=text)
    assert seen == [text.encode()]
    assert result["secret_ref"] is None and result["encoding"] == "plain"


def test_tool_only_reads_ledger_secrets_and_sanitizes_proxy_reason():
    cloud = FakeCloud()
    service = LeakTest(ddb=cloud, secrets_client=cloud,
        client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(403, json={"reason": cloud.value, "event_id": cloud.value}))))
    with pytest.raises(ValueError):
        service.run(agent_id=cloud.agent, secret_ref="burn/shared/absent")
    assert cloud.secret_reads == []
    result = service.run(agent_id=cloud.agent)
    assert result["reason"] == "proxy_rejected" and result["event_id"] is None


def test_http_api_encodings_cors_and_safe_errors():
    service = Mock()
    service.run.return_value = {"blocked": True, "encoding": "plain"}
    client = TestClient(create_app(service))
    assert client.get("/encodings").json() == {"encodings": list(ENCODINGS)}
    response = client.post("/leak-test", json={"agent_id": "agent-" + secrets.token_hex(4)},
                           headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    value = secrets.token_hex(32)
    response = client.post("/leak-test", json={"agent_id": value, "free_text": value})
    assert response.status_code == 422 and value not in response.text
    service.run.side_effect = RuntimeError(value)
    response = client.post("/leak-test", json={"agent_id": "agent-" + secrets.token_hex(4), "free_text": value})
    assert response.status_code == 503 and value not in response.text
