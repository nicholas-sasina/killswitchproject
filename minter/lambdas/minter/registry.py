"""burn-credentials: one row per credential (never the value, only metadata)."""
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

import aws
from burn_contracts.names import CREDENTIALS_TABLE


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(secret_ref: str) -> dict | None:
    return aws.table(CREDENTIALS_TABLE).get_item(Key={"secret_ref": secret_ref}).get("Item")


def put(secret_ref: str, owner: str, secret_type: str, version: int, allowed_hosts: list[str],
        **extra) -> dict:
    row = {
        "secret_ref": secret_ref, "owner_agent_id": owner, "secret_type": secret_type,
        "version": version, "status": "active", "created_at": _now(),
        "allowed_hosts": allowed_hosts, **extra,
    }
    aws.table(CREDENTIALS_TABLE).put_item(Item=row)
    return row


def bump_version(row: dict) -> int:
    """Record a rotation: version + 1, stamped with the time."""
    version = int(row["version"]) + 1
    aws.table(CREDENTIALS_TABLE).update_item(
        Key={"secret_ref": row["secret_ref"]},
        UpdateExpression="SET version = :v, rotated_at = :t",
        ExpressionAttributeValues={":v": version, ":t": _now()},
    )
    return version


def owned_by(owner: str) -> list[dict]:
    resp = aws.table(CREDENTIALS_TABLE).query(
        IndexName="by_owner", KeyConditionExpression=Key("owner_agent_id").eq(owner)
    )
    return resp["Items"]


def flag(secret_ref: str, flagged_by: str) -> bool:
    """Mark a credential as seen by a burned agent. False if it is not registered."""
    try:
        aws.table(CREDENTIALS_TABLE).update_item(
            Key={"secret_ref": secret_ref},
            UpdateExpression="SET #s = :f, flagged_at = :t, flagged_by = :a",
            ConditionExpression="attribute_exists(secret_ref)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":f": "flagged", ":t": _now(), ":a": flagged_by},
        )
        return True
    except aws.dynamodb().meta.client.exceptions.ConditionalCheckFailedException:
        return False


def delete(secret_ref: str) -> None:
    aws.table(CREDENTIALS_TABLE).delete_item(Key={"secret_ref": secret_ref})
