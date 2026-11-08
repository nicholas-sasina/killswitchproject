"""A fake sign-in service: the token and revoke endpoints of the people pool's managed login."""
import base64
import hashlib
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def jwt(claims: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


class FakeCognito:
    def __init__(self, email="ada@example.com", lifetime=3600):
        self.email, self.lifetime = email, lifetime
        self.requests: list[tuple[str, dict]] = []
        self.challenge: str | None = None  # what the browser was sent; the code exchange must prove it
        self.refresh_tokens: set[str] = set()
        self.revoked: list[str] = []
        self.n = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                form = {k: v[0] for k, v in urllib.parse.parse_qs(
                    self.rfile.read(int(self.headers.get("content-length") or 0)).decode()).items()}
                outer.requests.append((self.path, form))
                status, body = outer.handle(self.path, form)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def tokens(self, lifetime=None):
        self.n += 1
        now = int(time.time())
        refresh = f"refresh-{self.n}"
        self.refresh_tokens.add(refresh)
        return {"id_token": jwt({"email": self.email, "sub": "u1", "exp": now + (self.lifetime if lifetime is None else lifetime),
                                 "n": self.n}),
                "access_token": f"access-{self.n}", "refresh_token": refresh, "expires_in": self.lifetime}

    def handle(self, path, form):
        if path == "/oauth2/revoke":
            self.revoked.append(form["token"])
            return 200, {}
        if path != "/oauth2/token":
            return 404, {}
        if form.get("grant_type") == "authorization_code":
            verifier = form.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if form.get("code") != "good-code" or challenge != self.challenge:
                return 400, {"error": "invalid_grant"}
            return 200, self.tokens()
        if form.get("grant_type") == "refresh_token":
            if form.get("refresh_token") not in self.refresh_tokens:
                return 400, {"error": "invalid_grant"}
            out = self.tokens()
            out.pop("refresh_token")  # Cognito does not return a new refresh token on refresh
            return 200, out
        return 400, {"error": "unsupported_grant_type"}
