"""Create disposable, generated test credentials in the local AWS emulator."""
import json
import os
from pathlib import Path
import secrets
import time

import boto3

from burn_contracts.fingerprint import fingerprint, shingle_fingerprints
from tls import create_tls


def main():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    for _ in range(60):
        try:
            ddb.meta.client.list_tables()
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("AWS emulator did not become ready")
    for name, keys in (("burn-sessions", ["session_id"]), ("burn-fingerprints", ["fingerprint"]),
                       ("burn-events", ["stream", "event_id"])):
        ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in keys],
            KeySchema=[{"AttributeName": key, "KeyType": "HASH" if i == 0 else "RANGE"} for i, key in enumerate(keys)])
    agent, session, key = "agent-" + secrets.token_hex(4), secrets.token_urlsafe(24), secrets.token_bytes(32)
    body = secrets.token_hex(16)
    value = "mpk_" + "live_" + body
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(
        Name="burn-system/fingerprint-key", SecretString=key.hex())
    ddb.Table("burn-sessions").put_item(Item={"session_id": session, "agent_id": agent, "status": "active"})
    metadata = {"secret_ref": f"burn/{agent}/api_key", "secret_type": "api_key", "owner_agent_id": agent,
                "part": "value", "version": 1, "status": "active"}
    table = ddb.Table("burn-fingerprints")
    table.put_item(Item={**metadata, "fingerprint": fingerprint(value, key), "kind": "exact"})
    shingles = shingle_fingerprints(body, key)
    with table.batch_writer() as writer:
        for index, fp in shingles:
            writer.put_item(Item={**metadata, "fingerprint": fp, "kind": "shingle",
                                 "index": index, "n_shingles": len(shingles)})
    path = Path("/test-data/runtime.json")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"agent": agent, "session": session, "value": value}, handle)
    create_tls(path.parent)
    print("Created disposable proxy test data.")


if __name__ == "__main__":
    main()
