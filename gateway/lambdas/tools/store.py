"""DynamoDB access for the mock tools: scenario data and agent messages.

burn-scenario items are written by the scenario loader: pk = "ticket#<id>",
"file#<path>", "customer#<id>", ...; attribute "data" holds the object exactly
as in the scenario YAML. Nothing scenario-specific lives in this code.
"""
import functools
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from burn_contracts.events import utc_now_iso
from burn_contracts.ids import new_ulid
from burn_contracts.names import MESSAGES_TABLE, REGION, SCENARIO_TABLE

OUTBOX = "outbox"  # burn-messages partition for notify() output


@functools.cache
def _dynamodb():
    return boto3.resource("dynamodb", region_name=REGION)


def _plain(value):
    """DynamoDB returns numbers as Decimal, which Lambda cannot JSON-encode."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def scenario_item(pk: str) -> dict | None:
    item = _dynamodb().Table(SCENARIO_TABLE).get_item(Key={"pk": pk}).get("Item")
    return _plain(item.get("data")) if item else None


def scenario_items(prefix: str) -> list[dict]:
    """All scenario objects whose pk starts with prefix (the table holds one small scenario)."""
    table = _dynamodb().Table(SCENARIO_TABLE)
    kwargs = {"FilterExpression": Attr("pk").begins_with(prefix)}
    items = []
    while True:
        page = table.scan(**kwargs)
        items += [_plain(i["data"]) for i in page.get("Items", []) if "data" in i]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def put_message(to_agent: str, from_agent: str, text: str, **extra) -> str:
    msg_id = new_ulid()  # sorts by time, so an inbox reads oldest first
    _dynamodb().Table(MESSAGES_TABLE).put_item(Item={
        "to_agent": to_agent, "msg_id": msg_id, "from_agent": from_agent,
        "text": text, "ts": utc_now_iso(), **extra,
    })
    return msg_id


def take_messages(to_agent: str) -> list[dict]:
    """Read and delete every message for to_agent, oldest first."""
    table = _dynamodb().Table(MESSAGES_TABLE)
    items = table.query(KeyConditionExpression=Key("to_agent").eq(to_agent), ConsistentRead=True).get("Items", [])
    for item in items:
        table.delete_item(Key={"to_agent": to_agent, "msg_id": item["msg_id"]})
    return items
