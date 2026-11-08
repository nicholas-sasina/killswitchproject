"""A small stateful Burn API for tests: roles, join tokens, instances, burns.

It follows contracts/burn-api.openapi.yaml for the routes the CLI uses. `fail` queues error statuses
for a route so tests can exercise retries.
"""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_TOKEN = secrets.token_hex(12)  # fresh each run; tests assert it never reaches any output


class FakeApi:
    def __init__(self):
        self.roles: dict[str, dict] = {}
        self.instances: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict, dict]] = []  # method, path, body, headers
        self.fail: dict[tuple[str, str], list[int]] = {}
        self.heartbeat_status = 200  # set to 409 to simulate a burn
        self.lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1].split("?")[0] == path]

    # -- routing -----------------------------------------------------------------------------
    def handle(self, method, path, query, body):
        parts = path.strip("/").split("/")
        if (method, path) == ("GET", "/roles"):
            return 200, {"roles": list(self.roles.values())}
        if method == "PUT" and parts[0] == "roles":
            existing = self.roles.get(parts[1])
            if existing and existing["managed_by"] != body.get("managed_by", "ui"):
                return 409, {"error": "role is managed elsewhere"}
            self.roles[parts[1]] = {**body, "managed_by": body.get("managed_by", "ui")}
            return 200, self.roles[parts[1]]
        if method == "POST" and parts[0] == "roles" and parts[-1] == "tokens":
            if parts[1] not in self.roles:
                return 404, {"error": "no such role"}
            token_id, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
            row = {"token_id": token_id, "role": parts[1], "uses": body.get("uses", 1),
                   "uses_left": body.get("uses", 1), "expires_at": "2030-01-01T00:00:00Z", "status": "active"}
            self.tokens[token_id] = {**row, "secret": secret}
            return 201, {**row, "token": f"bjt_{token_id}.{secret}"}
        if (method, path) == ("GET", "/instances"):
            rows = [i for i in self.instances.values()
                    if query.get("role", i["role"]) == i["role"] and query.get("status", i["status"]) == i["status"]]
            return 200, {"instances": rows}
        if (method, path) == ("POST", "/burns"):
            return self._burns(body)
        if (method, path) == ("POST", "/burn"):
            return 200, {"agent_id": body["agent_id"], "revoked": True, "sessions_revoked": [],
                         "rotated": [f"burn/{body['agent_id']}/api_key"], "flagged": []}
        if (method, path) == ("POST", "/sessions"):
            return self._session(body)
        if method == "POST" and parts[0] == "instances" and parts[-1] == "heartbeat":
            return self.heartbeat_status, {"error": "instance is not active"} if self.heartbeat_status == 409 else {}
        if method == "POST" and parts[0] == "instances" and parts[-1] == "exit":
            if parts[1] in self.instances:
                self.instances[parts[1]]["status"] = "exited"
            return 200, {}
        return 404, {"error": "not found"}

    def add_instance(self, session_id, role, parent=None, token_id=None, status="active"):
        self.instances[session_id] = {
            "session_id": session_id, "agent_id": f"agent-{role}-{secrets.token_hex(3)}", "role": role,
            "status": status, "parent_session": parent, "token_id": token_id,
            "created_at": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:01Z"}
        return self.instances[session_id]

    def _session(self, body):
        role = body["role"]
        if role not in self.roles:
            return 404, {"error": "no such role"}
        token_id = None
        if "enroll_token" in body:
            token_id = body["enroll_token"].removeprefix("bjt_").split(".")[0]
            row = self.tokens.get(token_id)
            if not row or body["enroll_token"] != f"bjt_{token_id}.{row['secret']}" or row["uses_left"] < 1:
                return 403, {"error": "join token invalid, expired or used up"}
            row["uses_left"] -= 1
        else:
            parent = self.instances.get(body.get("parent_session"))
            if not parent or role not in self.roles[parent["role"]].get("may_spawn", []):
                return 403, {"error": "role is not in the parent's may_spawn"}
        session_id = "sess_" + secrets.token_hex(8)
        inst = self.add_instance(session_id, role, body.get("parent_session"), token_id)
        return 201, {"session_id": session_id, "agent_id": inst["agent_id"], "role": role,
                     "badge_ref": f"burn/{inst['agent_id']}/badge", "parent_session": body.get("parent_session"),
                     "token_id": token_id, "tools": self.roles[role].get("tools", []), "egress": None,
                     "credentials": [{"secret_ref": f"burn/{inst['agent_id']}/{t}", "type": t, "owner": "agent"}
                                     for t in self.roles[role].get("credentials", [])]}

    def _burns(self, body):
        scope, ident = body["scope"], body["id"]
        if scope == "instance":
            hit = [i for i in self.instances.values() if i["session_id"] == ident]
        elif scope == "role":
            hit = [i for i in self.instances.values() if i["role"] == ident and i["status"] == "active"]
        elif scope == "token":
            hit = [i for i in self.instances.values() if i["token_id"] == ident]
        else:
            hit, frontier = [], {ident}
            while frontier:
                nxt = [i for i in self.instances.values() if i["session_id"] in frontier or i["parent_session"] in frontier]
                new = {i["session_id"] for i in nxt} - {i["session_id"] for i in hit}
                hit = [i for i in self.instances.values() if i in nxt or i in hit]
                frontier = new
        if not hit:
            return 404, {"error": "nothing matches"}
        for i in hit:
            i["status"] = "revoked"
        return 200, {"scope": scope, "id": ident, "burned": [
            {"agent_id": i["agent_id"], "revoked": True, "sessions_revoked": [i["session_id"]],
             "rotated": [f"burn/{i['agent_id']}/api_key"], "flagged": []} for i in hit], "failed": []}

    # -- http plumbing -----------------------------------------------------------------------
    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                path, _, qs = self.path.partition("?")
                query = dict(p.split("=", 1) for p in qs.split("&") if p)
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                with outer.lock:
                    outer.requests.append((self.command, self.path, body, dict(self.headers)))
                    if self.headers.get("x-burn-demo-token") != API_TOKEN:
                        return self._send(401, {"error": "missing or invalid x-burn-demo-token"})
                    queue = outer.fail.get((self.command, path))
                    if queue:
                        return self._send(queue.pop(0), {"error": "injected failure"})
                    self._send(*outer.handle(self.command, path, query, body))

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = _serve

            def log_message(self, *args):
                pass

        return Handler
