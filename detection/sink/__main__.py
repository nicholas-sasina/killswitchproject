"""Small partner webhook: receipt size and SHA-256 only."""
from collections import deque
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import threading


class Receipts:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.items = deque(maxlen=1000)
        self.lock = threading.Lock()

    def add(self, size: int, digest: str) -> dict:
        item = {"size": size, "sha256": digest}
        with self.lock:
            self.items.append(item)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as handle:
                    handle.write(json.dumps(item) + "\n")
        return item

    def snapshot(self):
        with self.lock:
            return list(self.items)


def handler(receipts: Receipts):
    class Webhook(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status: int, data: dict):
            raw = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"ok": True})
            elif self.path == "/receipts":
                self.reply(200, {"receipts": receipts.snapshot()})
            else:
                self.receive()

        def receive(self):
            length = self.headers.get("Content-Length", "0")
            if not length.isdecimal() or int(length) > 1_048_576 or self.headers.get("Transfer-Encoding"):
                self.reply(413, {"reason": "unsupported_size"})
                return
            remaining, size = int(length), 0
            digest = hashlib.sha256()
            while remaining:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    self.reply(400, {"reason": "incomplete_body"})
                    return
                digest.update(chunk)
                remaining -= len(chunk)
                size += len(chunk)
            self.reply(200, receipts.add(size, digest.hexdigest()))

        do_POST = do_PUT = do_PATCH = do_DELETE = receive

    return Webhook


def main():
    receipts = Receipts(Path(os.environ.get("BURN_SINK_LOG", "/data/requests.jsonl")))
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8081"))), handler(receipts))
    if os.environ.get("BURN_SINK_TLS_CERT"):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(os.environ["BURN_SINK_TLS_CERT"], os.environ["BURN_SINK_TLS_KEY"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
