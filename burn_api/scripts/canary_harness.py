"""In-memory harness for the canary path: mock provider -> Burn API -> minter. No AWS.

The three real Lambda handlers run in-process against moto, wired the way they
are deployed (provider dispatches the incident to the Burn API, the Burn API
calls the minter). Session start, {{canary}} resolution and the leak are simulated:
- session creation calling the canary hook,
- the Gateway resolving {{canary}} and writing the ledger row,
- the leak itself and the attacker decoding it.

Used by tests/test_canary_containment.py, and runnable as the local demo with
real timings (steps 5-7 of the demo):

    cd burn_api && uv run python scripts/canary_harness.py --workers 20
"""
import argparse
import base64
import gzip
import json
import os
import pathlib
import secrets
import sys
import time
import types

REPO = pathlib.Path(__file__).resolve().parents[2]
for sub in ("minter/lambdas/minter", "minter/lambdas/provider", "burn_api/lambdas/api"):
    if str(REPO / sub) not in sys.path:
        sys.path.append(str(REPO / sub))

if "badges" not in sys.modules:  # the identity helper is bundled at deploy time
    _stub = types.ModuleType("badges")
    _stub.create_badge = lambda agent_id, session_id: {
        "client_id": "client-" + secrets.token_hex(4), "client_secret": secrets.token_hex(8),
        "token_url": "https://example.invalid/token", "scope": "burn/tools"}
    _stub.delete_badge = lambda client_id: None
    sys.modules["badges"] = _stub

import boto3  # noqa: E402

AGENT_POLICY = {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": "s3:ListAllMyBuckets", "Resource": "*"}]}
ENV = {
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
    "PROVIDER_HOST": "provider.example",
    "BOUNDARY_POLICY_ARN": "arn:aws:iam::123456789012:policy/burn-agent-boundary",
    "AGENT_POLICY_JSON": json.dumps(AGENT_POLICY),
}
TABLES = {  # name: (pk, sk, [(index, pk, sk)]) as created by infra/burn_infra/data.py
    "burn-events": ("stream", "event_id", [("by_agent", "agent_id", "event_id")]),
    "burn-exposures": ("agent_id", "secret_ref_or_fp", []),
    "burn-credentials": ("secret_ref", None, [("by_owner", "owner_agent_id", None)]),
    "burn-fingerprints": ("fingerprint", None, [("by_secret", "secret_ref", None)]),
    "burn-sessions": ("session_id", None, [("by_agent", "agent_id", None), ("by_client", "client_id", None)]),
    "burn-scenario": ("pk", None, []),
    "burn-roles": ("name", None, []),
}


def _keys(pk, sk):
    return [{"AttributeName": pk, "KeyType": "HASH"}] + ([{"AttributeName": sk, "KeyType": "RANGE"}] if sk else [])


def create_tables() -> None:
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    for name, (pk, sk, gsis) in TABLES.items():
        attrs = {pk, sk, *(a for _, g_pk, g_sk in gsis for a in (g_pk, g_sk))} - {None}
        args = {"TableName": name, "KeySchema": _keys(pk, sk), "BillingMode": "PAY_PER_REQUEST",
                "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "S"} for a in sorted(attrs)]}
        if gsis:
            args["GlobalSecondaryIndexes"] = [{"IndexName": i, "KeySchema": _keys(g_pk, g_sk),
                                               "Projection": {"ProjectionType": "ALL"}} for i, g_pk, g_sk in gsis]
        ddb.create_table(**args)


def worker_ids(count: int) -> list[str]:
    """Instances of role worker, each with its own agent id (agent-worker-<6 hex>)."""
    from burn_contracts.names import instance_agent_id
    return [instance_agent_id("worker", f"{i:06x}") for i in range(1, count + 1)]


class Stack:
    """Call inside an active moto mock_aws() with ENV set. wire(setattr) connects the Lambdas."""

    def __init__(self, workers: int = 3, extra_agents: dict | None = None):
        import auth, db, aws, provider_app  # noqa: E401 (imported late: they need the env)
        create_tables()
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="burn-system/fingerprint-key", SecretString=secrets.token_hex(32))
        self.token = secrets.token_hex(8)
        sm.create_secret(Name="burn-system/demo-token", SecretString=self.token)
        aws.reset(); db.reset(); auth.reset(); provider_app._key_cache.clear()  # noqa: E702
        self.agents = {a: {"id": a, "role": "worker", "tools": ["read_config"], "credentials": ["api_key", "aws_key"]}
                       for a in worker_ids(workers)}
        self.agents.update(extra_agents or {})
        scenario = self.table("burn-scenario")
        scenario.put_item(Item={"pk": "meta", "data": {"agent_ids": list(self.agents)}})
        for agent_id, agent in self.agents.items():
            scenario.put_item(Item={"pk": f"agent#{agent_id}", "data": agent})
        self.incidents = []  # every Burn API canary result, for timings and assertions

    # ---- wiring (what infra does in AWS) -------------------------------------------------
    def minter_call(self, method, path, body=None, query=None):
        import minter_app, minter_client  # noqa: E401
        out = minter_app.handler({"method": method, "path": path, "body": body or {}, "query": query or {}})
        if out["statusCode"] != 200:
            raise minter_client.MinterError(f"minter {method} {path} failed: {out['statusCode']}",
                                            status=out["statusCode"], detail=out["body"].get("error"))
        return out["body"]

    def invoke(self, function_name, payload, wait):
        import handler, minter_app  # noqa: E401
        if function_name == "burn-api":
            self.incidents.append(handler.handler(payload))  # async in AWS; inline here
            return None
        if function_name == "burn-minter":
            return minter_app.handler(payload)
        raise AssertionError(function_name)

    def wire(self, setattr_):
        import dispatch, minter_client  # noqa: E401
        setattr_(minter_client, "call", self.minter_call)
        setattr_(dispatch, "invoke", self.invoke)

    # ---- simulated steps ------------------------------------------------------------------
    def api(self, method, path, body=None):
        import handler
        event = {"requestContext": {"http": {"method": method, "path": path}},
                 "headers": {"x-burn-demo-token": self.token}, "body": json.dumps(body) if body else None}
        out = handler.handler(event)
        return out["statusCode"], json.loads(out["body"])

    def start_session(self, agent_id: str) -> dict:
        """POST /sessions, then the canary hook (simulated)."""
        status, session = self.api("POST", "/sessions", {"agent_id": agent_id})
        assert status == 201, session
        session["canary"] = self.minter_call("POST", f"/sessions/{session['session_id']}/canary",
                                             {"agent_id": agent_id})
        return session

    def add_sibling_session(self, agent_id: str) -> dict:
        """Another live session on the SAME agent id. Every instance normally has its own agent id, so
        this should not happen; it exercises the guard that never widens a canary burn (simulated)."""
        session_id = "sess_" + secrets.token_hex(8)
        self.table("burn-sessions").put_item(Item={
            "session_id": session_id, "agent_id": agent_id,
            "client_id": "client-" + secrets.token_hex(4), "status": "active", "tools": [], "send_to": [],
            "created_at": "2026-11-07T00:00:00+00:00"})
        canary = self.minter_call("POST", f"/sessions/{session_id}/canary", {"agent_id": agent_id})
        return {"session_id": session_id, "agent_id": agent_id, "canary": canary}

    def secret(self, ref: str, stage: str = "AWSCURRENT") -> str:
        return boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
            SecretId=ref, VersionStage=stage)["SecretString"]

    def expose(self, agent_id: str, secret_ref: str, via: str = "read_config", owner: str | None = None) -> None:
        """The Gateway's ledger write when a tool response shows a credential (simulated)."""
        from burn_contracts import fingerprint as fp
        key = fp.key_from_hex(self.secret("burn-system/fingerprint-key"))
        owner_agent = secret_ref.split("/")[1]
        self.table("burn-exposures").put_item(Item={
            "agent_id": agent_id, "secret_ref_or_fp": secret_ref, "secret_ref": secret_ref,
            "fingerprint": fp.fingerprint(self.secret(secret_ref), key), "secret_type": "other",
            "owner": owner or ("shared" if owner_agent == "shared" else "agent"),
            "owner_agent_id": None if owner_agent == "shared" else owner_agent,
            "source": "tool_response", "via": via, "first_seen": "2026-11-07T20:00:00.000Z"})

    def read_canary(self, session: dict) -> str:
        """The agent reads a document whose {{canary}} the Gateway filled for ITS session (simulated)."""
        ref = session["canary"]["canary_ref"]
        self.expose(session["agent_id"], ref)
        return self.secret(ref)

    # ---- the monitored provider -----------------------------------------------------------
    def present(self, key: str, ip: str = "198.51.100.23", via_session: str | None = None) -> tuple[int, dict]:
        """From outside Burn by default; via_session = sent through the egress proxy for that session."""
        import provider_app
        import session_proof
        from burn_contracts import fingerprint as fp
        headers = {"authorization": f"Bearer {key}"}
        if via_session:  # the header the egress proxy adds (simulated)
            hmac_key = fp.key_from_hex(self.secret("burn-system/fingerprint-key"))
            headers[session_proof.HEADER] = session_proof.make(via_session, hmac_key)
        resp = provider_app.handler({"rawPath": "/v1/whoami", "headers": headers,
                                     "requestContext": {"http": {"sourceIp": ip}}})
        return resp["statusCode"], json.loads(resp["body"])

    def set_role(self, session_id: str, role: str, **policy) -> None:
        """Give a session a stored role (as role-based instances have) with the given policy."""
        self.table("burn-roles").put_item(Item={"name": role, "tools": [], "managed_by": "ui", **policy})
        self.table("burn-sessions").update_item(Key={"session_id": session_id}, UpdateExpression="SET #r = :r",
                                                ExpressionAttributeNames={"#r": "role"},
                                                ExpressionAttributeValues={":r": role})

    # ---- reads ----------------------------------------------------------------------------
    def table(self, name: str):
        return boto3.resource("dynamodb", region_name="us-east-1").Table(name)

    def session_status(self, session_id: str) -> str:
        return self.table("burn-sessions").get_item(Key={"session_id": session_id})["Item"]["status"]

    def credential(self, ref: str) -> dict:
        return self.table("burn-credentials").get_item(Key={"secret_ref": ref}).get("Item") or {}

    def events(self, action: str | None = None) -> list[dict]:
        items = self.table("burn-events").scan()["Items"]
        items = [i for i in items if i.get("stream") == "all" and (action is None or i["action"] == action)]
        return sorted(items, key=lambda e: e["event_id"])


def leak_gzip_base64(value: str) -> str:
    """The worker's leak in an encoding the exit scanner does not undo (simulated)."""
    return base64.b64encode(gzip.compress(value.encode())).decode()


def attacker_decode(blob: str) -> str:
    return gzip.decompress(base64.b64decode(blob)).decode()


def run_demo(workers: int) -> dict:
    """Steps 1-8 of the team demo, locally; returns measured timings and statuses."""
    from moto import mock_aws
    os.environ.update(ENV)
    with mock_aws():
        stack = Stack(workers=workers)
        stack.wire(setattr)
        sessions = {a: stack.start_session(a) for a in stack.agents}
        victim = sorted(sessions)[len(sessions) // 3]
        old_key = stack.secret(f"burn/{victim}/api_key")
        stolen = attacker_decode(leak_gzip_base64(stack.read_canary(sessions[victim])))
        start = time.perf_counter()
        status, _ = stack.present(stolen)
        total_ms = round((time.perf_counter() - start) * 1000, 1)
        result = stack.incidents[-1]
        others = [stack.session_status(s["session_id"]) for a, s in sessions.items() if a != victim]
        return {
            "workers": workers, "victim": victim, "provider_status": status, "outcome": result["outcome"],
            "victim_session": stack.session_status(sessions[victim]["session_id"]),
            "other_sessions_active": others.count("active"), "other_sessions": len(others),
            "old_api_key_status": stack.present(old_key)[0],
            "ms_incident_to_revoked": result["timings_ms"]["revoked"],
            "ms_incident_to_rotation_done": result["timings_ms"]["rotation_done"],
            "ms_provider_request_total_inline": total_ms,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    for _ in range(args.runs):
        print(json.dumps(run_demo(args.workers)))
