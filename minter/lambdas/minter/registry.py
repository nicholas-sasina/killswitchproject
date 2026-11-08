"""burn-credentials: one row per credential (never the value, only metadata)."""
from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

import aws
from burn_contracts.names import CREDENTIALS_TABLE


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(secret_ref: str, consistent: bool = False) -> dict | None:
    return aws.table(CREDENTIALS_TABLE).get_item(
        Key={"secret_ref": secret_ref}, ConsistentRead=consistent).get("Item")


def put_if_absent(row: dict) -> bool:
    """Write a new row only if no row has this secret_ref yet. False if one already exists."""
    try:
        aws.table(CREDENTIALS_TABLE).put_item(
            Item=row, ConditionExpression="attribute_not_exists(secret_ref)")
        return True
    except conditional_failed():
        return False


def update_status(secret_ref: str, expected: str, status: str) -> dict:
    """Move a row from one status to another; raises conditional_failed() if it is not at `expected`."""
    return aws.table(CREDENTIALS_TABLE).update_item(
        Key={"secret_ref": secret_ref},
        UpdateExpression="SET #s = :new",
        ConditionExpression="#s = :expected",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":new": status, ":expected": expected},
        ReturnValues="ALL_NEW",
    )["Attributes"]


def conditional_failed():
    """The exception DynamoDB raises when a ConditionExpression is false."""
    return aws.dynamodb().meta.client.exceptions.ConditionalCheckFailedException


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
    kwargs = {"IndexName": "by_owner", "KeyConditionExpression": Key("owner_agent_id").eq(owner)}
    items = []
    while True:  # canaries add one row per session, so an agent can outgrow one page
        resp = aws.table(CREDENTIALS_TABLE).query(**kwargs)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def delete(secret_ref: str) -> None:
    aws.table(CREDENTIALS_TABLE).delete_item(Key={"secret_ref": secret_ref})
