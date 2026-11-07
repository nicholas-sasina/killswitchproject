"""Live check of the deployed gateway, end to end.

    cd gateway && uv run python scripts/smoke.py

Creates a temporary agent (badge, session row, a real api_key from the
minter, one scenario file that references it), then checks:
  1. tools/list shows only the agent's allowed tools; an allowed tool works,
     another is refused.
  2. Reading the file records the key in the agent's ledger.
  3. Sending the key (base64-encoded) to a peer agent is blocked.
  4. A clean message goes through and the peer inherits the ledger.
  5. After the session is revoked in DynamoDB, the next call is refused.
Prints the events, then deletes everything it created. No token, secret or
credential value is ever printed.
"""
import base64
import json
import pathlib
import secrets
import sys
import urllib.parse
import urllib.request

import boto3
from boto3.dynamodb.conditions import Key

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import badges  # noqa: E402

REGION = "us-east-1"
TOOLS = ["list_tickets", "read_config", "send_message"]


def ssm_value(name: str) -> str:
    return boto3.client("ssm", region_name=REGION).get_parameter(Name=name)["Parameter"]["Value"]


def minter(method: str, path: str, body: dict | None = None) -> dict:
    payload = {"method": method, "path": path, "body": body or {}}
    response = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName="burn-minter", Payload=json.dumps(payload).encode())
    result = json.loads(response["Payload"].read())
    if result.get("statusCode") != 200:
        raise RuntimeError(f"minter {method} {path} failed: {result.get('statusCode')}")
    return result["body"]


def get_token(badge: dict) -> str:
    basic = base64.b64encode(f"{badge['client_id']}:{badge['client_secret']}".encode()).decode()
    request = urllib.request.Request(
        badge["token_url"],
        data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": badge["scope"]}).encode(),
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())["access_token"]


class Mcp:
    """Minimal MCP-over-HTTP client: one JSON-RPC POST per call."""

    def __init__(self, url: str, token: str):
        self.url, self.token, self.session_header, self.next_id = url, token, None, 1

    def call(self, method: str, params: dict | None = None, notify: bool = False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            body["id"], self.next_id = self.next_id, self.next_id + 1
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session_header:
            headers["Mcp-Session-Id"] = self.session_header
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            self.session_header = response.headers.get("Mcp-Session-Id") or self.session_header
            raw = response.read().decode()
        if notify or not raw.strip():
            return None
        if raw.lstrip().startswith("data:") or "\ndata:" in raw:  # server-sent events
            raw = next(line[5:] for line in raw.splitlines() if line.startswith("data:"))
        return json.loads(raw)

    def tool(self, name: str, arguments: dict) -> tuple[bool, str]:
        result = self.call("tools/call", {"name": name, "arguments": arguments})["result"]
        return bool(result.get("isError")), result["content"][0]["text"]


class Run:
    """Everything the smoke test creates, so it can all be deleted at the end."""

    def __init__(self):
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        self.tables = {name: dynamodb.Table(name) for name in (
            "burn-sessions", "burn-events", "burn-exposures", "burn-scenario", "burn-messages")}
        tag = secrets.token_hex(3)
        self.agent, self.peer = f"agent-smoke-{tag}", f"agent-smoke-{tag}-peer"
        self.session_id = f"sess_smoke_{secrets.token_hex(4)}"
        self.file_path = f"smoke-{tag}/app.env"
        self.badge = None
        self.failures = []

    def check(self, ok: bool, label: str) -> None:
        print(("PASS " if ok else "FAIL ") + label)
        if not ok:
            self.failures.append(label)

    def events(self, agent_id: str) -> list[dict]:
        items = self.tables["burn-events"].query(
            IndexName="by_agent", KeyConditionExpression=Key("agent_id").eq(agent_id))["Items"]
        return sorted(items, key=lambda e: e["event_id"])

    def ledger(self, agent_id: str) -> list[dict]:
        return self.tables["burn-exposures"].query(KeyConditionExpression=Key("agent_id").eq(agent_id))["Items"]

    def setup(self) -> None:
        minter("POST", f"/agents/{self.agent}/credentials", {"types": ["api_key"]})
        self.tables["burn-scenario"].put_item(Item={"pk": f"file#{self.file_path}", "data": {
            "path": self.file_path, "content": f"PROVIDER_API_KEY={{{{cred:{self.agent}:api_key}}}}\n"}})
        self.badge = badges.create_badge(self.agent, self.session_id)
        self.tables["burn-sessions"].put_item(Item={
            "session_id": self.session_id, "agent_id": self.agent, "client_id": self.badge["client_id"],
            "status": "active", "tools": TOOLS, "send_to": [self.peer]})
        print(f"created {self.agent} (session {self.session_id}) with a minted api_key")

    def cleanup(self) -> None:
        """Each step runs even if an earlier one fails, so nothing is left behind silently."""
        steps = [
            ("badge", lambda: self.badge and badges.delete_badge(self.badge["client_id"])),
            ("session", lambda: self.tables["burn-sessions"].delete_item(Key={"session_id": self.session_id})),
            ("scenario file", lambda: self.tables["burn-scenario"].delete_item(Key={"pk": f"file#{self.file_path}"})),
            ("credential", self.delete_credential),
            ("ledgers, events, messages", self.delete_records),
        ]
        for label, step in steps:
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - report and keep cleaning
                print(f"WARN cleanup of {label} failed: {type(exc).__name__}: {exc}"[:300])
        print("cleaned up badge, session, credential, scenario file, ledgers, messages and events")

    def delete_credential(self) -> None:
        try:
            minter("DELETE", f"/agents/{self.agent}/credentials")
            return
        except RuntimeError as exc:
            print(f"WARN {exc}; deleting the minted key's secret and rows directly")
        ref = f"burn/{self.agent}/api_key"
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        fingerprints = dynamodb.Table("burn-fingerprints")
        for row in fingerprints.query(IndexName="by_secret", KeyConditionExpression=Key("secret_ref").eq(ref))["Items"]:
            fingerprints.delete_item(Key={"fingerprint": row["fingerprint"]})
        dynamodb.Table("burn-credentials").delete_item(Key={"secret_ref": ref})
        boto3.client("secretsmanager", region_name=REGION).delete_secret(SecretId=ref, ForceDeleteWithoutRecovery=True)

    def delete_records(self) -> None:
        for agent_id in (self.agent, self.peer):
            for row in self.ledger(agent_id):
                self.tables["burn-exposures"].delete_item(
                    Key={"agent_id": agent_id, "secret_ref_or_fp": row["secret_ref_or_fp"]})
            for event in self.events(agent_id):
                self.tables["burn-events"].delete_item(Key={"stream": event["stream"], "event_id": event["event_id"]})
        messages = self.tables["burn-messages"]
        for message in messages.query(KeyConditionExpression=Key("to_agent").eq(self.peer))["Items"]:
            messages.delete_item(Key={"to_agent": self.peer, "msg_id": message["msg_id"]})


def exercise(run: Run) -> None:
    mcp = Mcp(ssm_value("/burn/gateway/url"), get_token(run.badge))
    mcp.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "burn-smoke", "version": "1"}})
    mcp.call("notifications/initialized", notify=True)

    names = sorted(t["name"] for t in mcp.call("tools/list")["result"]["tools"])
    run.check(names == sorted(TOOLS), f"tools/list shows only the allowed tools: {names}")
    is_error, text = mcp.tool("list_tickets", {})
    run.check(not is_error and "tickets" in json.loads(text), "allowed tool list_tickets works")
    is_error, text = mcp.tool("read_ticket", {"ticket_id": "T-1"})
    run.check(is_error and text.startswith("BURN:"), f"tool outside the allowlist refused: {text}")

    is_error, text = mcp.tool("read_config", {"path": run.file_path})
    key = json.loads(text)["content"].strip().split("=", 1)[1]  # held in memory only
    refs = [row.get("secret_ref") for row in run.ledger(run.agent)]
    run.check(not is_error and refs == [f"burn/{run.agent}/api_key"], f"read_config recorded the key in the ledger: {refs}")

    is_error, text = mcp.tool("send_message", {"to": run.peer, "text": base64.b64encode(key.encode()).decode()})
    run.check(is_error and text.startswith("BURN: blocked"), f"base64-encoded key in send_message blocked: {text}")

    is_error, text = mcp.tool("send_message", {"to": run.peer, "text": "the provider outage is fixed"})
    peer_refs = [row.get("secret_ref") for row in run.ledger(run.peer)]
    run.check(not is_error and json.loads(text)["delivered"] and peer_refs == refs,
              f"clean message delivered and the peer inherited the ledger: {peer_refs}")

    run.tables["burn-sessions"].update_item(
        Key={"session_id": run.session_id}, UpdateExpression="SET #s = :r",
        ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":r": "revoked"})
    print("session revoked in DynamoDB (the token itself is still valid at Cognito)")
    is_error, text = mcp.tool("list_tickets", {})
    run.check(is_error and text.startswith("BURN:"), f"next call after revocation refused: {text}")

    all_events = run.events(run.agent) + run.events(run.peer)
    for e in sorted(all_events, key=lambda e: e["event_id"]):
        print(f"  {e['agent_id']:<28} {e['source']}/{e['action']:<9} tool={e.get('tool')} "
              f"ref={e.get('secret_ref')} encoding={e.get('encoding')} | {e.get('detail')}")
    actions = sorted(e["action"] for e in all_events)
    run.check(actions == ["blocked", "inherited", "recorded", "refused", "refused"], f"events written: {actions}")


def main() -> int:
    run = Run()
    try:
        run.setup()
        exercise(run)
    finally:
        run.cleanup()
    print("SMOKE OK" if not run.failures else f"SMOKE FAILED: {run.failures}")
    return 1 if run.failures else 0


if __name__ == "__main__":
    sys.exit(main())
