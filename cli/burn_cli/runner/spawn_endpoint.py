"""A local endpoint where the agent asks for a child instance: POST {role, command} -> {session_id, agent_id}."""
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from ..api import ApiError


class SpawnEndpoint:
    def __init__(self, spawn: Callable[[str, list[str]], dict], local_token: str = ""):
        self._spawn, self._local_token = spawn, local_token
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}/spawn"

    def start(self) -> "SpawnEndpoint":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                supplied = self.headers.get("authorization", "").removeprefix("Bearer ")
                if not hmac.compare_digest(supplied.encode(), outer._local_token.encode()):
                    return self._send(401, {"error": "missing or wrong local token (send BURN_LOCAL_TOKEN as a bearer)"})
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                    role, command = body["role"], body["command"]
                    if not isinstance(role, str) or not isinstance(command, list) or not command \
                            or not all(isinstance(c, str) for c in command):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    return self._send(400, {"error": "expected {role: string, command: [string, ...]}"})
                if self.path != "/spawn":
                    return self._send(404, {"error": "not found"})
                try:
                    self._send(200, outer._spawn(role, command))
                except ApiError as exc:
                    self._send(exc.status or 502, {"error": str(exc)})
                except OSError as exc:
                    self._send(500, {"error": f"cannot start the command: {exc.strerror}"})

            def _send(self, status: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        return Handler
