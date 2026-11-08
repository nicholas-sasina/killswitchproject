"""Isolated interpreter for real product modules with moto and a network deny guard.

Only synthetic, per-run credential fixtures exist in memory. Output is observations,
not payloads, tokens, event bodies, or credential values.
"""
import argparse
import base64
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from unittest.mock import patch


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def provenance(root, paths):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *paths], cwd=root, text=True)
    hashes = {}
    for folder in paths:
        for path in sorted((root/folder).rglob("*.py")):
            if ".venv" not in path.parts and "__pycache__" not in path.parts:
                hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    combined = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"source_commit": commit, "source_dirty": bool(dirty.strip()),
            "product_source_sha256": combined, "product_file_sha256": hashes,
            "measured_at": now(), "scope": "local-emulator"}


def source_paths(root, *paths):
    for path in reversed(("contracts/python", "detection", *paths)):
        sys.path.insert(0, str(root/path))


def create_tables(specs):
    import boto3
    db = boto3.client("dynamodb", region_name="us-east-1")
    for name, (pk, sk, indexes) in specs.items():
        attributes = {pk} | ({sk} if sk else set()) | {key for _, key in indexes}
        schema = [{"AttributeName": pk, "KeyType": "HASH"}]
        if sk:
            schema.append({"AttributeName": sk, "KeyType": "RANGE"})
        args = dict(TableName=name, BillingMode="PAY_PER_REQUEST", KeySchema=schema,
                    AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in attributes])
        if indexes:
            args["GlobalSecondaryIndexes"] = [{"IndexName": name, "KeySchema": [{"AttributeName": key, "KeyType": "HASH"}],
                                               "Projection": {"ProjectionType": "ALL"}} for name, key in indexes]
        db.create_table(**args)


def references(root, trials):
    source_paths(root, "gateway/lambdas/interceptor")
    import boto3
    import detection
    import interceptor
    import shapes
    from burn_contracts import names
    from burn_contracts.fingerprint import fingerprint
    result = provenance(root, ("gateway/lambdas/interceptor", "contracts/python", "detection/burn_detect"))
    create_tables({
        names.SESSIONS_TABLE: ("session_id", None, [("by_client", "client_id"), ("by_agent", "agent_id")]),
        names.FINGERPRINTS_TABLE: ("fingerprint", None, []),
        names.EXPOSURES_TABLE: ("agent_id", "secret_ref_or_fp", []),
        names.EVENTS_TABLE: ("stream", "event_id", []),
    })
    key = secrets.token_bytes(32)
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(
        Name=names.FINGERPRINT_KEY_SECRET, SecretString=key.hex())
    db = boto3.resource("dynamodb", region_name="us-east-1")
    records = []
    withheld = tool_errors = reference_tokens = referenced_ledger_entries = 0
    for _ in range(trials):
        suffix = secrets.token_hex(4)
        agent, session, client = "agent-probe-"+suffix, "sess_probe_"+suffix, "client_probe_"+suffix
        db.Table(names.SESSIONS_TABLE).put_item(Item={"session_id": session, "agent_id": agent,
            "client_id": client, "status": "active", "tools": ["read_config"], "send_to": []})
        planted = {}
        for owner in (agent, "shared"):
            value = "mpk_live_"+secrets.token_hex(16)
            ref = f"burn/{owner}/probe_{suffix}"
            planted[ref] = value
            db.Table(names.FINGERPRINTS_TABLE).put_item(Item={"fingerprint": fingerprint(value, key),
                "secret_ref": ref, "secret_type": "api_key", "owner_agent_id": owner,
                "kind": "exact", "part": "value", "version": 1, "status": "active"})
        text = "\n".join(f"API_KEY_{i}={value}" for i, value in enumerate(planted.values()))
        recognized = {f.secret_ref for f in detection.scan(text) if f.secret_ref in planted}
        payload = base64.urlsafe_b64encode(json.dumps({"client_id": client}).encode()).rstrip(b"=").decode()
        rpc_id = secrets.randbelow(1_000_000)
        event = {"interceptorInputVersion": "1.0", "mcp": {
            "gatewayRequest": {"body": {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
                "params": {"name": "burn-tools___read_config", "arguments": {"path": "probe.env"}}},
                "headers": {"Authorization": f"Bearer e30.{payload}.fixture", "X-Amzn-Trace-Id": suffix}},
            "gatewayResponse": {"statusCode": 200, "body": {"jsonrpc": "2.0", "id": rpc_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": False}}}}}
        output = interceptor.handler(event, None)["mcp"]["transformedGatewayResponse"]
        if output["statusCode"] != 200:
            raise RuntimeError("Gateway response probe was not a successful tool response")
        if not isinstance(output.get("body"), dict) or not isinstance(output["body"].get("result"), dict):
            raise RuntimeError("Gateway probe returned no valid tool result")
        withheld += shapes.is_burn_denial(output["body"])
        tool_errors += output["body"]["result"].get("isError") is True
        output_text = "\n".join(shapes.result_texts(output["body"]))
        remaining = {f.secret_ref for f in detection.scan(output_text) if f.secret_ref in recognized}
        remaining.update(ref for ref in recognized if planted[ref] in output_text)
        reference_tokens += sum(f"{{{{burn:ref:{ref}}}}}" in output_text for ref in recognized)
        for ref in recognized:
            row = db.Table(names.EXPOSURES_TABLE).get_item(
                Key={"agent_id": agent, "secret_ref_or_fp": ref}, ConsistentRead=True).get("Item", {})
            referenced_ledger_entries += row.get("status") == "referenced" and row.get("source_ref") == "read_config:probe.env"
        records.append({"event_id": "reference-probe-"+suffix, "recognized": len(recognized),
                        "in_context": len(remaining)})
    result.update(records=records, checks={"responses": len(records), "withheld_responses": withheld,
        "tool_errors": tool_errors, "reference_tokens": reference_tokens,
        "referenced_ledger_entries": referenced_ledger_entries}, notes=" ".join([
        "Real Gateway RESPONSE handler, detection and ledger writes execute against moto; tool output and JWT claims are synthetic fixtures.",
        "Gateway JWT authorization and tool execution are outside this probe. No deployed AWS endpoint or model is called.",
        "Counts cover registered plaintext API credentials in text tool output. A failing baseline target is reported without changing product behavior.",
        "Withheld responses and tool errors are counted separately; zero exposure alone does not establish successful reference resolution.",
        "Reference tokens and ledger entries marked referenced with the expected source_ref are counted separately. Tool-side reference resolution is outside this response probe.",
    ]))
    return result


def canaries(root, trials, *, negative_checks=False, policy_checks=False):
    source_paths(root)
    path = root/"burn_api/scripts/canary_harness.py"
    if not path.is_file():
        raise RuntimeError("Canary owner's harness is absent in the selected source checkout")
    spec = importlib.util.spec_from_file_location("canary_harness", path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    from boto3.dynamodb.conditions import Key
    os.environ.update(harness.ENV)
    result = provenance(root, ("burn_api/lambdas", "burn_api/scripts", "minter/lambdas", "contracts/python"))
    stack = harness.Stack(workers=trials+1+int(negative_checks)+4*int(policy_checks))
    stack.wire(setattr)
    sessions = {agent: stack.start_session(agent) for agent in stack.agents}
    records = []
    rejection_count = old_key_rejections = 0
    first_stolen = None
    def agent_events(agent):
        events, page = [], {}
        while True:
            response = stack.table("burn-events").query(IndexName="by_agent",
                KeyConditionExpression=Key("agent_id").eq(agent), **page)
            events.extend(response.get("Items", []))
            if not response.get("LastEvaluatedKey"):
                return events
            page = {"ExclusiveStartKey": response["LastEvaluatedKey"]}

    for agent in sorted(sessions)[:trials]:
        session = sessions[agent]
        old_api_key = stack.secret(f"burn/{agent}/api_key")
        # Explicitly simulated neighbouring steps, implemented by the canary owner.
        value = harness.attacker_decode(harness.leak_gzip_base64(stack.read_canary(session)))
        if first_stolen is None:
            first_stolen = value
        used_at = now()
        status, _ = stack.present(value)
        rejection_count += status == 401
        old_key_rejections += stack.present(old_api_key)[0] == 401
        events = [e for e in agent_events(agent) if e.get("action") == "canary_used"
                  and e.get("secret_ref") == session["canary"]["canary_ref"]]
        row = stack.table("burn-sessions").get_item(Key={"session_id": session["session_id"]})["Item"]
        record = {"event_id": events[0]["event_id"] if events else "undetected-use-"+secrets.token_hex(8), "used_at": used_at}
        if events:
            record["detected_at"] = events[0]["ts"]
        if row.get("status") == "revoked":
            record["burned_at"] = row["revoked_at"]
        records.append(record)
    positive_incidents = list(stack.incidents)
    negative = {}
    if negative_checks:
        first_agent = sorted(sessions)[0]
        api_ref = f"burn/{first_agent}/api_key"
        before_version = int(stack.credential(api_ref)["version"])
        before_events = len([e for e in agent_events(first_agent) if e.get("action") == "canary_used"])
        before_incidents = len(stack.incidents)
        rejected = sum(stack.present(first_stolen)[0] == 401 for _ in range(2))
        negative["duplicate_replay"] = {
            "presentations": 2, "provider_rejections": rejected,
            "new_incidents": len(stack.incidents)-before_incidents,
            "extra_api_rotations": int(stack.credential(api_ref)["version"])-before_version,
            "extra_canary_events": len([e for e in agent_events(first_agent) if e.get("action") == "canary_used"])-before_events,
        }

        import minter_client
        failed_agent = sorted(sessions)[trials]
        failed_session = sessions[failed_agent]
        old_key = stack.secret(f"burn/{failed_agent}/api_key")
        failed_value = stack.read_canary(failed_session)
        original_call = stack.minter_call
        def fail_rotation(method, path, body=None, query=None):
            if path.endswith("/rotate"):
                raise minter_client.MinterError("injected local rotation failure")
            return original_call(method, path, body, query)
        before_failure_incidents = len(stack.incidents)
        with patch.object(minter_client, "call", fail_rotation):
            provider_status, _ = stack.present(failed_value)
        incident = stack.incidents[-1] if len(stack.incidents) > before_failure_incidents else {}
        negative["rotation_failure"] = {
            "provider_rejected": provider_status == 401,
            "session_revoked": stack.session_status(failed_session["session_id"]) == "revoked",
            "outcome": incident.get("outcome", "no_incident"),
            "rotated_credentials": len(incident.get("rotated", [])),
            "old_api_key_accepted": stack.present(old_key)[0] == 200,
            "incomplete_containment_flagged": any(e.get("action") == "flagged" and "containment incomplete" in e.get("detail", "")
                                                  for e in agent_events(failed_agent)),
        }
    policies = {}
    if policy_checks:
        for index, case in enumerate(("own_session", "other_session", "report_only", "ambiguous_exposure")):
            agent = sorted(sessions)[trials+int(negative_checks)+index]
            session = sessions[agent]
            api_ref = f"burn/{agent}/api_key"
            old_key = stack.secret(api_ref)
            value = stack.read_canary(session)
            before_version = int(stack.credential(api_ref)["version"])
            before_incidents = len(stack.incidents)
            if case == "report_only":
                stack.set_role(session["session_id"], "probe-report-only", auto_burn=False)
            elif case == "ambiguous_exposure":
                # This holder deliberately does not appear in the scenario agent list.
                stack.expose("agent-unlisted-abcdef", session["canary"]["canary_ref"])
            via = (session["session_id"] if case == "own_session" else
                   sessions[sorted(sessions)[-1]]["session_id"] if case == "other_session" else None)
            status, _ = stack.present(value, via_session=via)
            new_incidents = stack.incidents[before_incidents:]
            policies[case] = {
                "provider_rejected": status == 401,
                "session_active": stack.session_status(session["session_id"]) == "active",
                "old_api_key_accepted": stack.present(old_key)[0] == 200,
                "new_incidents": len(new_incidents),
                "api_rotations": int(stack.credential(api_ref)["version"])-before_version,
                "canary_events": sum(e.get("action") == "canary_used" for e in agent_events(agent)),
                "outcome": new_incidents[-1]["outcome"] if new_incidents else "not_dispatched",
            }
    control = sessions[sorted(sessions)[-1]]
    outcomes = {}
    for incident in positive_incidents:
        outcome = incident.get("outcome", "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0)+1
    result.update(records=records, negative_checks=negative, policy_checks=policies, checks={"provider_rejections": rejection_count,
                                          "rotated_old_api_key_rejections": old_key_rejections,
                                          "control_session_active": stack.session_status(control["session_id"]) == "active",
                                          "containment_outcomes": outcomes},
                  notes=" ".join([
        "Real provider, Burn API and minter Lambda handlers execute against moto from the selected canary source commit.",
        "Enrollment/identity, canary planting/exposure, leak and attacker decoding are simulated by the owner's canary_harness.py.",
        "Lambda incident dispatch is synchronous in-process here. Observed use-to-revoke times exclude real AWS networking and async dispatch; they are not deployed end-to-end latency.",
        "Optional negative_checks are fault-injection/replay observations, excluded from the ordinary-use detection-rate denominator; a revoked session with failed credential rotation is not complete containment.",
        "Optional policy_checks measure alert-only cases separately; session-origin headers and additional ledger holders are synthetic harness inputs, not a full proxy-to-provider network flow.",
    ]))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("references", "canaries"))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--trials", type=int, required=True)
    parser.add_argument("--negative-checks", action="store_true")
    parser.add_argument("--policy-checks", action="store_true")
    args = parser.parse_args()
    root = args.source_root.resolve()
    if not 1 <= args.trials <= 100:
        raise ValueError("Invalid trial count")
    from moto import mock_aws
    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_SESSION_TOKEN="testing",
                      AWS_DEFAULT_REGION="us-east-1", AWS_EC2_METADATA_DISABLED="true", BURN_TOOL_TARGET="burn-tools")
    os.environ.pop("AWS_ENDPOINT_URL", None)
    os.environ.pop("BURN_STAGE", None)
    logging.disable(logging.CRITICAL)
    def deny(*args, **kwargs):
        raise RuntimeError("Network access is forbidden in an offline measurement probe")
    # Fail closed even if an unmocked client, dependency, or source revision tries a socket.
    with ExitStack() as stack:
        for method in ("connect", "connect_ex"):
            stack.enter_context(patch.object(socket.socket, method, deny))
        stack.enter_context(mock_aws())
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if (args.negative_checks or args.policy_checks) and args.kind != "canaries":
                raise ValueError("additional checks apply only to canaries")
            result = references(root, args.trials) if args.kind == "references" else canaries(
                root, args.trials, negative_checks=args.negative_checks, policy_checks=args.policy_checks)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": "Offline product probe failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
