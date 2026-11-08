"""The local endpoint where the agent asks its supervisor for things, on one port:

- POST /spawn {role, command} -> {session_id, agent_id}: start a child instance;
- POST /progress {item, state}: report task progress, sent with the next heartbeat (when given a queue).
  "done" is answered at once. "started" is answered once Burn has recorded it, because the agent must not act on
  an item Burn does not know it touched: 200 recorded, 409 the instance was burned, 400 Burn refused it, or 503
  when Burn has not confirmed it in time (it stays queued). Only a 200 means go ahead.
"""
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from burn_contracts.progress import ProgressError

from ..api import ApiError
from .progress import ProgressQueue


class SpawnEndpoint:
    def __init__(self, spawn: Callable[[str, list[str]], dict], local_token: str = "",
                 progress: ProgressQueue | None = None, progress_wait: float = 15.0):
        self._spawn, self._local_token, self._progress = spawn, local_token, progress
        self._progress_wait = progress_wait  # how long a start may wait to be recorded
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        base = f"http://127.0.0.1:{self._server.server_port}"
        self.url, self.progress_url = f"{base}/spawn", f"{base}/progress"

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
                except ValueError:
                    body = None
                if self.path == "/progress" and outer._progress is not None:
                    return self._report(body)
                if self.path != "/spawn":
                    return self._send(404, {"error": "not found"})
                try:
                    role, command = body["role"], body["command"]
                    if not isinstance(role, str) or not isinstance(command, list) or not command \
                            or not all(isinstance(c, str) for c in command):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    return self._send(400, {"error": "expected {role: string, command: [string, ...]}"})
                try:
                    self._send(200, outer._spawn(role, command))
                except ApiError as exc:
                    self._send(exc.status or 502, {"error": str(exc)})
                except OSError as exc:
                    self._send(500, {"error": f"cannot start the command: {exc.strerror}"})

            def _report(self, body):
                if not isinstance(body, dict):
                    return self._send(400, {"error": "expected a JSON object {item, state}"})
                try:
                    queued = outer._progress.add(body)
                except ProgressError as exc:
                    return self._send(400, {"error": str(exc)})  # names the broken rule
                if body["state"] != "started":
                    return self._send(200, {"queued": queued})
                outcome, detail = outer._progress.wait_recorded(body["item"], outer._progress_wait)
                if outcome == "recorded":
                    return self._send(200, {"queued": queued, "recorded": True})
                if outcome == "gone":
                    return self._send(409, {"error": "the instance was burned or has exited: do not start this item"})
                if outcome == "refused":
                    return self._send(400, {"error": f"Burn refused this start ({detail}): do not start this item"})
                self._send(503, {"error": "Burn has not recorded this start yet (it stays queued): "
                                          "do not start this item"})

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
