"""DynamoDB access shared by every route: table handles, event writer, JSON helpers."""
import json
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from burn_contracts import names
from burn_contracts.events import make_event

_resource = None


def resource():
    # Created lazily so tests can start moto before the first call.
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb", region_name=names.REGION)
    return _resource


def reset():
    """Forget cached clients (tests only)."""
    global _resource
    _resource = None


def table(name: str):
    return resource().Table(name)


def query_all(tbl, **kwargs) -> list[dict]:
    """Follow pagination so callers always get every matching item."""
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = tbl.query(**kwargs)
        items += page["Items"]
        start = page.get("LastEvaluatedKey")
        if not start:
            return items


def sessions_for_agent(agent_id: str) -> list[dict]:
    return query_all(
        table(names.SESSIONS_TABLE), IndexName="by_agent",
        KeyConditionExpression=Key("agent_id").eq(agent_id),
    )


def get_agent(agent_id: str) -> dict | None:
    """The agent object exactly as written in the scenario file, or None."""
    item = table(names.SCENARIO_TABLE).get_item(Key={"pk": f"agent#{agent_id}"}).get("Item")
    return item["data"] if item else None


def write_event(**fields) -> dict:
    """Build a schema-valid event and append it to the global stream."""
    event = make_event(**fields)
    table(names.EVENTS_TABLE).put_item(Item={"stream": names.EVENTS_STREAM, **event})
    return event


def _default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not JSON serializable: {type(value)}")


def dumps(body) -> str:
    return json.dumps(body, default=_default)
