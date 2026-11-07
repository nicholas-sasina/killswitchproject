"""burn-fingerprints: what the exit check compares against (see contracts/README.md).

Only HMACs are stored. A rotated credential keeps its rows with status
"rotated", so a leak of an old value is still caught and attributed.
"""
from boto3.dynamodb.conditions import Key

import aws
import vault
from burn_contracts import fingerprint as fp
from burn_contracts.names import FINGERPRINT_KEY_SECRET, FINGERPRINTS_TABLE


def key() -> bytes:
    return fp.key_from_hex(vault.get(FINGERPRINT_KEY_SECRET))


def register(secret_ref: str, owner: str, secret_type: str, version: int, parts,
             extra: dict | None = None) -> int:
    """Write fingerprint rows for one credential version.

    parts: (part_name, text, exact, shingles). Only the secret bits are
    fingerprinted: e.g. for an api key the random body, not the public prefix.
    extra: more safe attributes for every row (canaries add canary/session_id/instance_id).
    """
    hmac_key = key()
    rows = {}
    for part, text, exact, shingles in parts:
        base = {
            "secret_ref": secret_ref, "owner_agent_id": owner, "secret_type": secret_type,
            "part": part, "version": version, "status": "active", **(extra or {}),
        }
        if exact:
            rows[fp.fingerprint(text, hmac_key)] = {**base, "kind": "exact"}
        if shingles:
            windows = fp.shingle_fingerprints(text, hmac_key)
            for index, digest in windows:
                rows.setdefault(digest, {**base, "kind": "shingle", "index": index,
                                         "n_shingles": len(windows)})
    with aws.table(FINGERPRINTS_TABLE).batch_writer() as batch:
        for digest, row in rows.items():
            batch.put_item(Item={"fingerprint": digest, **row})
    return len(rows)


def get(digest: str) -> dict | None:
    return aws.table(FINGERPRINTS_TABLE).get_item(Key={"fingerprint": digest}).get("Item")


def _rows_for(secret_ref: str) -> list[dict]:
    kwargs = {"IndexName": "by_secret", "KeyConditionExpression": Key("secret_ref").eq(secret_ref)}
    items = []
    while True:
        resp = aws.table(FINGERPRINTS_TABLE).query(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def mark_rotated(secret_ref: str, version: int) -> None:
    """Flip one version's rows to status=rotated (kept, never deleted)."""
    with aws.table(FINGERPRINTS_TABLE).batch_writer() as batch:
        for row in _rows_for(secret_ref):
            if int(row["version"]) == version:
                batch.put_item(Item={**row, "status": "rotated"})


def delete_all(secret_ref: str) -> None:
    with aws.table(FINGERPRINTS_TABLE).batch_writer() as batch:
        for row in _rows_for(secret_ref):
            batch.delete_item(Key={"fingerprint": row["fingerprint"]})
