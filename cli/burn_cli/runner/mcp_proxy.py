"""A local MCP endpoint that forwards to the Gateway and adds the instance's badge token.

The agent talks plain HTTP to 127.0.0.1 and never sees the badge secret or the token. Every forwarded request
calls `on_activity`, which feeds the instance's heartbeat.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

from .badge import BadgeToken, TokenRefused, TokenUnavailable

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "authorization", "content-length", "accept-encoding"}


class McpProxy:
    def __init__(self, gateway_url: str, badge: BadgeToken, on_activity: Callable[[], None] = lambda: None):
        parts = urlsplit(gateway_url)
        self._origin, self._base_path = f"{parts.scheme}://{parts.netloc}", parts.path
        self._badge, self._on_activity = badge, on_activity
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}{self._base_path or '/'}"

    def start(self) -> "McpProxy":
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _forward(self):
                outer._on_activity()
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else None
                try:
                    token = outer._badge.get()
                except TokenRefused as exc:
                    return self._fail(401, f"BURN: {exc}")
                except TokenUnavailable as exc:
                    return self._fail(502, str(exc))
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
                headers["Authorization"] = f"Bearer {token}"  # replaces anything the agent sent
                req = urllib.request.Request(outer._origin + self.path, method=self.command, data=body, headers=headers)
                try:
                    upstream = urllib.request.urlopen(req, timeout=120)
                except urllib.error.HTTPError as err:
                    upstream = err  # the Gateway's own refusal is passed on unchanged
                except (urllib.error.URLError, TimeoutError) as exc:
                    return self._fail(502, f"cannot reach the Gateway ({getattr(exc, 'reason', exc)})")
                with upstream:
                    self.send_response(upstream.getcode())
                    for k, v in upstream.headers.items():
                        if k.lower() not in HOP_BY_HOP:
                            self.send_header(k, v)
                    self.send_header("connection", "close")
                    self.end_headers()
                    read = getattr(upstream, "read1", None) or upstream.read
                    while chunk := read(8192):
                        self.wfile.write(chunk)  # streamed, so server-sent events pass through as they arrive
                        self.wfile.flush()

            def _fail(self, status: int, message: str):
                data = ('{"error": %s}' % json.dumps(message)).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _forward

            def log_message(self, *args):
                pass

        return Handler
