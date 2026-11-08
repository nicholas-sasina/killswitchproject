"""DynamoDB access shared by every route: table handles, event writer, JSON helpers."""
import json
import threading
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from burn_contracts import names
from burn_contracts.events import make_event

_local = threading.local()
_session: boto3.session.Session | None = None
_session_lock = threading.Lock()


def resource():
    # One resource per thread (boto3 resources are not thread-safe; scoped burns and the exposure counts use
    # pools), all made from ONE session. A session holds the parsed service models, about 10 MB, so a session
    # per thread filled the 256 MB Lambda once the 16 count threads were up. Sessions are not thread-safe
    # either, so resources are made from it one at a time. Created lazily so tests can start moto first.
    if getattr(_local, "resource", None) is None:
        global _session
        with _session_lock:
            if _session is None:
                _session = boto3.session.Session()
            _local.resource = _session.resource("dynamodb", region_name=names.REGION)
    return _local.resource


def reset():
    """Forget cached clients (tests only)."""
    global _local, _session
    _local, _session = threading.local(), None


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


def scan_all(tbl, **kwargs) -> list[dict]:
    """Follow pagination on a filtered scan."""
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = tbl.scan(**kwargs)
        items += page["Items"]
        start = page.get("LastEvaluatedKey")
        if not start:
            return items


def get_scenario_agent(agent_id: str) -> dict | None:
    """The agent object exactly as written in the scenario file, or None."""
    item = table(names.SCENARIO_TABLE).get_item(Key={"pk": f"agent#{agent_id}"}).get("Item")
    return item["data"] if item else None


def get_agent(agent_id: str) -> dict | None:
    """A scenario agent, or else an agent-shaped view of a role instance, or None.

    Instances are not in the scenario file. Their agent id is resolved through their
    session (the newest one), so burn, ledger and blast radius work for them unchanged.
    """
    agent = get_scenario_agent(agent_id)
    if agent is not None:
        return agent
    rows = [s for s in sessions_for_agent(agent_id) if s.get("role")]
    if not rows:
        return None
    row = max(rows, key=lambda s: s.get("created_at", ""))
    return {"id": agent_id, "role": row["role"], "tools": list(row.get("tools", [])),
            "credentials": list(row.get("credentials", [])), "send_to": []}


# Lookups over burn-sessions by role, parent or token. They are filtered scans, which is fine at
# swarm scale (hundreds of rows). A GSI per attribute (by_role, by_parent, by_token) is the next
# step if the table grows; only these three functions would change.

# The parent and token lookups are strongly consistent: a scoped burn must see a child row that a
# concurrent spawn or join has just written.

def sessions_for_role(role: str) -> list[dict]:
    return scan_all(table(names.SESSIONS_TABLE), FilterExpression=Attr("role").eq(role))


def sessions_for_parents(parent_sessions: list[str]) -> list[dict]:
    found = []
    for i in range(0, len(parent_sessions), 100):  # IN takes at most 100 values
        found += scan_all(table(names.SESSIONS_TABLE),
                          FilterExpression=Attr("parent_session").is_in(parent_sessions[i:i + 100]),
                          ConsistentRead=True)
    return found


def sessions_for_token(token_id: str) -> list[dict]:
    return scan_all(table(names.SESSIONS_TABLE), FilterExpression=Attr("token_id").eq(token_id),
                    ConsistentRead=True)


def write_event(**fields) -> dict:
    """Build a schema-valid event and append it to the global stream.

    `actor` defaults to whoever made the current request (caller.py), so every route attributes its events.
    """
    import caller
    if fields.get("actor") is None and caller.get().actor:
        fields["actor"] = caller.get().actor
    event = make_event(**fields)
    table(names.EVENTS_TABLE).put_item(Item={"stream": names.EVENTS_STREAM, **event})
    return event


def _default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not JSON serializable: {type(value)}")


def dumps(body) -> str:
    return json.dumps(body, default=_default)
