"""Live check of the deployed gateway: badge in, tools out, revoke, refused.

    cd gateway && uv run python scripts/smoke.py

1. Creates a temporary agent session: a badge (Cognito app client) and a
   burn-sessions row allowing two tools.
2. Gets a token with the badge and lists tools (only the allowed two appear).
3. Calls an allowed tool (works) and a tool outside the allowlist (refused).
4. Flips the session to "revoked" in DynamoDB and calls again: refused.
5. Prints the refused events from burn-events, then deletes everything it
   created. No token, secret or credential value is ever printed.
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
ALLOWED = ["list_tickets", "read_ticket"]


def ssm_value(name: str) -> str:
    return boto3.client("ssm", region_name=REGION).get_parameter(Name=name)["Parameter"]["Value"]


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


def main() -> int:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    sessions, events = dynamodb.Table("burn-sessions"), dynamodb.Table("burn-events")
    agent_id = f"agent-smoke-{secrets.token_hex(3)}"
    session_id = f"sess_smoke_{secrets.token_hex(4)}"
    gateway_url = ssm_value("/burn/gateway/url")
    badge = badges.create_badge(agent_id, session_id)
    print(f"created {agent_id} session {session_id} badge client {badge['client_id']}")
    sessions.put_item(Item={"session_id": session_id, "agent_id": agent_id, "client_id": badge["client_id"],
                            "status": "active", "tools": ALLOWED, "send_to": []})
    failures = []

    def check(ok: bool, label: str) -> None:
        print(("PASS " if ok else "FAIL ") + label)
        if not ok:
            failures.append(label)

    try:
        mcp = Mcp(gateway_url, get_token(badge))
        mcp.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "burn-smoke", "version": "1"}})
        mcp.call("notifications/initialized", notify=True)

        names = sorted(t["name"] for t in mcp.call("tools/list")["result"]["tools"])
        check(names == sorted(ALLOWED), f"tools/list shows only the allowed tools: {names}")

        is_error, text = mcp.tool("list_tickets", {})
        check(not is_error and "tickets" in json.loads(text), "allowed tool list_tickets works")

        is_error, text = mcp.tool("read_config", {"path": "anything"})
        check(is_error and text.startswith("BURN:"), f"tool outside the allowlist refused: {text}")

        sessions.update_item(Key={"session_id": session_id}, UpdateExpression="SET #s = :r",
                             ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":r": "revoked"})
        print("session revoked in DynamoDB (the token itself is still valid at Cognito)")
        is_error, text = mcp.tool("list_tickets", {})
        check(is_error and text.startswith("BURN:"), f"next call after revocation refused: {text}")

        mine = events.query(IndexName="by_agent", KeyConditionExpression=Key("agent_id").eq(agent_id))["Items"]
        refused = [e for e in mine if e["action"] == "refused"]
        for event in sorted(refused, key=lambda e: e["event_id"]):
            print(f"  event {event['event_id']} {event['source']}/{event['action']} tool={event.get('tool')} "
                  f"detail={event.get('detail')}")
        check(len(refused) == 2, "two refused events written to burn-events")
    finally:
        badges.delete_badge(badge["client_id"])
        sessions.delete_item(Key={"session_id": session_id})
        mine = events.query(IndexName="by_agent", KeyConditionExpression=Key("agent_id").eq(agent_id))["Items"]
        for event in mine:
            events.delete_item(Key={"stream": event["stream"], "event_id": event["event_id"]})
        print(f"cleaned up badge, session row and {len(mine)} events")

    print("SMOKE OK" if not failures else f"SMOKE FAILED: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
