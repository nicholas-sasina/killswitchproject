"""HTTP-shaped direct-invoke minter. All values and history are local and fake."""

import re
import secrets
from datetime import datetime

from burn_contracts.events import utc_now_iso
from burn_contracts.names import agent_secret_ref, is_agent_id

if __package__:
    from .mock_data import Credentials
else:
    from mock_data import Credentials


def key_activity(agent_id: str, credentials: Credentials, since=None) -> dict:
    ref = agent_secret_ref(agent_id, "aws_key")
    row = credentials.rows.get(ref)
    if not row:
        return {"agent_id": agent_id, "keys": [], "events": [], "note": "Mock CloudTrail data."}
    version = row["version"]
    keys = [{"key_ref": f"{ref}#v{v}", "key_hint": "…" + secrets.token_hex(2).upper(),
             "status": "active" if v == version else "inactive"}
            for v in range(1, version + 1)]
    events = [{"ts": utc_now_iso(), "key_ref": f"{ref}#v1", "key_hint": keys[0]["key_hint"][-4:], "event_name": name,
               "event_source": source, "resource": None, "source_ip": "127.0.0.1",
               "error_code": None}
              for name, source in [("ListBuckets", "s3.amazonaws.com"),
                                   ("GetCallerIdentity", "sts.amazonaws.com"),
                                   ("InvokeModel", "bedrock.amazonaws.com")]]
    if since is not None:
        events = [event for event in events if datetime.fromisoformat(event["ts"]) >= since]
    return {"agent_id": agent_id, "keys": keys, "events": events, "note": "Mock CloudTrail data."}


class MinterMock:
    def __init__(self):
        self.credentials = Credentials()

    def handle(self, event: dict) -> dict:
        method, path = event.get("method"), event.get("path", "")
        body = event.get("body") or {}
        if not isinstance(body, dict):
            return self.response(400, {"error": "body must be an object"})
        match = re.fullmatch(r"/agents/([^/]+)/(credentials|rotate|key-activity)", path)
        if match:
            agent_id, operation = match.groups()
            if not is_agent_id(agent_id):
                return self.response(400, {"error": "Invalid agent id"})
            if operation == "credentials" and method == "POST":
                types = body.get("types")
                if not isinstance(types, list) or any(t not in ("api_key", "aws_key") for t in types):
                    return self.response(400, {"error": "types must contain api_key or aws_key"})
                result = {"credentials": self.credentials.issue(agent_id, list(dict.fromkeys(types)))}
            elif operation == "credentials" and method == "DELETE":
                result = {"deleted": self.credentials.delete(agent_id)}
            elif operation == "rotate" and method == "POST":
                refs = body.get("exposed_refs", [])
                if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                    return self.response(400, {"error": "exposed_refs must be strings"})
                result = self.credentials.rotate(agent_id, refs)
            elif operation == "key-activity" and method == "GET":
                query = event.get("query") or {}
                try:
                    since = datetime.fromisoformat(query["since"]) if "since" in query else None
                    if since is not None and since.tzinfo is None:
                        raise ValueError("Timezone required")
                except (TypeError, ValueError):
                    return self.response(400, {"error": "since must be an ISO timestamp with a timezone"})
                result = key_activity(agent_id, self.credentials, since)
            else:
                return self.response(405, {"error": "Method not allowed"})
        elif path == "/shared/credentials" and method == "POST":
            name, kind, template = (body.get(key) for key in ("name", "type", "template"))
            if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]{1,40}", name)
                    or kind not in ("other", "api_key") or not isinstance(template, str)):
                return self.response(400, {"error": "Invalid shared credential request"})
            row = self.credentials.shared(name, kind, template)
            result = {"secret_ref": row["secret_ref"], "version": row["version"]}
        elif path == "/shared/credentials" and method == "DELETE":
            result = {"deleted": self.credentials.delete("shared")}
        else:
            return self.response(404, {"error": "Unknown route"})
        return self.response(200, result)

    @staticmethod
    def response(code, body):
        return {"statusCode": code, "body": body}


_minter = MinterMock()


def handle(event: dict) -> dict:
    return _minter.handle(event)


if __name__ == "__main__":
    print("In-memory minter ready. Import minter_mock.handle(event) to invoke it.")
