"""Development stand-in for the badge issuer, to run agents against the MCP mock.

Answers the OAuth client_credentials request with the token `mock-<client_id>`,
which is the bearer the MCP mock accepts. Use the agent id as the client id.

    uv run python scripts/mock_badge_issuer.py [port]
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from base64 import b64decode


class Issuer(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        basic = self.headers.get("Authorization", "").removeprefix("Basic ")
        client_id = b64decode(basic).decode().split(":")[0] if basic else ""
        body = json.dumps({"access_token": f"mock-{client_id}", "expires_in": 3600, "token_type": "Bearer"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8702), Issuer).serve_forever()
