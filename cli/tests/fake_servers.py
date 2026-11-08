"""A fake Gateway and a fake badge issuer for `burn run` tests."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Recorder:
    def __init__(self, respond):
        self.seen: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                outer.seen.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
                status, payload = respond(self.headers, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _serve

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def gateway():
    return Recorder(lambda headers, body: (200, {"ok": True}))


def issuer(expires_in=3600, status=200):
    """Answers client_credentials with token-<n>; remembers the client id and secret it was given."""
    state = {"n": 0, "clients": []}

    def respond(headers, body):
        user, _, password = base64.b64decode(headers["Authorization"].removeprefix("Basic ")).decode().partition(":")
        state["clients"].append((user, password))
        if status != 200:
            return status, {"error": "invalid_client"}
        state["n"] += 1
        return 200, {"access_token": f"token-{state['n']}", "expires_in": expires_in, "token_type": "Bearer"}

    rec = Recorder(respond)
    rec.state = state
    return rec
