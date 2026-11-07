"""Throwaway badges and sessions, a token fetcher and a small async MCP client.

Everything the eval creates is named agent-eval-<random> and removed by `Throwaway.close()`,
even if the run fails. Nothing here prints a token, a client secret or a credential.
"""
import asyncio
import base64
import json
import secrets
import sys
import time
from pathlib import Path

import boto3
import httpx
from botocore.exceptions import ClientError

# The gateway folder owns badge creation; reuse it rather than duplicate Cognito calls.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gateway"))
import badges  # noqa: E402

REGION = "us-east-1"
TOOLS = ["list_tickets", "read_ticket"]
THROTTLES = {"TooManyRequestsException", "ThrottlingException", "LimitExceededException"}


def ssm_value(name: str) -> str:
    return boto3.client("ssm", region_name=REGION).get_parameter(Name=name)["Parameter"]["Value"]


def with_backoff(fn, *args, tries: int = 8, **kwargs):
    """Cognito rate-limits client creation; retry with growing waits."""
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except ClientError as err:
            if err.response["Error"]["Code"] not in THROTTLES or attempt == tries - 1:
                raise
            time.sleep(min(10, 0.5 * 2 ** attempt))


class Throwaway:
    """N active sessions, each with its own badge. Use as a context manager."""

    def __init__(self, count: int):
        self.count = count
        self.items: list[dict] = []   # {agent_id, session_id, badge}
        self.sessions = boto3.resource("dynamodb", region_name=REGION).Table("burn-sessions")

    def open(self, workers: int = 4) -> "Throwaway":
        from concurrent.futures import ThreadPoolExecutor

        def make(_):
            agent_id = f"agent-eval-{secrets.token_hex(3)}"
            session_id = f"sess_eval_{secrets.token_hex(4)}"
            badge = with_backoff(badges.create_badge, agent_id, session_id)
            item = {"agent_id": agent_id, "session_id": session_id, "badge": badge}
            self.items.append(item)   # recorded before the row exists so close() always cleans up
            self.sessions.put_item(Item={"session_id": session_id, "agent_id": agent_id,
                                         "client_id": badge["client_id"], "status": "active",
                                         "tools": TOOLS, "send_to": []})
            return item

        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(make, range(self.count)))
        return self

    def close(self) -> dict:
        events = boto3.resource("dynamodb", region_name=REGION).Table("burn-events")
        removed = {"badges": 0, "sessions": 0, "events": 0}
        for item in self.items:
            removed["badges"] += bool(with_backoff(badges.delete_badge, item["badge"]["client_id"]))
            self.sessions.delete_item(Key={"session_id": item["session_id"]})
            removed["sessions"] += 1
            rows = events.query(IndexName="by_agent",
                                KeyConditionExpression=boto3.dynamodb.conditions.Key("agent_id").eq(item["agent_id"]))["Items"]
            for row in rows:
                events.delete_item(Key={"stream": row["stream"], "event_id": row["event_id"]})
            removed["events"] += len(rows)
        return removed

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        print("cleaned up", self.close())


async def fetch_token(http: httpx.AsyncClient, badge: dict) -> str:
    basic = base64.b64encode(f"{badge['client_id']}:{badge['client_secret']}".encode()).decode()
    for attempt in range(6):
        r = await http.post(badge["token_url"], data={"grant_type": "client_credentials", "scope": badge["scope"]},
                            headers={"Authorization": f"Basic {basic}"})
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()["access_token"]
        await asyncio.sleep(0.5 * 2 ** attempt)
    raise RuntimeError("token endpoint kept rate-limiting")


class Mcp:
    """One agent's MCP-over-HTTP connection. call() returns (seconds, is_error, status)."""

    def __init__(self, http: httpx.AsyncClient, url: str, token: str):
        self.http, self.url, self.token = http, url, token
        self.mcp_session, self.next_id, self.last_refused = None, 1, False

    async def rpc(self, method: str, params: dict | None = None, notify: bool = False):
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            body["id"], self.next_id = self.next_id, self.next_id + 1
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json, text/event-stream"}
        if self.mcp_session:
            headers["Mcp-Session-Id"] = self.mcp_session
        start = time.perf_counter()
        r = await self.http.post(self.url, json=body, headers=headers)
        elapsed = time.perf_counter() - start
        self.mcp_session = r.headers.get("Mcp-Session-Id") or self.mcp_session
        return elapsed, r

    async def start(self) -> None:
        _, response = await self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "burn-eval", "version": "1"}})
        response.raise_for_status()
        _, response = await self.rpc("notifications/initialized", notify=True)
        response.raise_for_status()

    async def call_tool(self, name: str, arguments: dict) -> tuple[float, bool, int]:
        elapsed, r = await self.rpc("tools/call", {"name": name, "arguments": arguments})
        try:
            messages = [r.json()]
        except ValueError:
            messages = []
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        messages.append(json.loads(line[5:].strip()))
                    except ValueError:
                        pass
        responses = [m for m in messages if isinstance(m, dict) and m.get("id") is not None
                     and ("result" in m or "error" in m)]
        is_error = r.status_code != 200 or not responses or any(
            "error" in m or not isinstance(m.get("result"), dict) or m["result"].get("isError") is True
            for m in responses)
        # Only a failed response with the interceptor's denial marker is a refusal.
        self.last_refused = r.status_code != 429 and is_error and "BURN:" in r.text
        return elapsed, is_error, r.status_code


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summarize_ms(seconds: list[float]) -> dict:
    ms = [s * 1000 for s in seconds]
    if not ms:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
    return {"n": len(ms), "p50_ms": round(percentile(ms, 0.5), 1), "p95_ms": round(percentile(ms, 0.95), 1),
            "mean_ms": round(sum(ms) / len(ms), 1)}
