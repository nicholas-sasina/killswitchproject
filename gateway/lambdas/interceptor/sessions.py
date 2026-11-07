"""Look up the badge session behind a call, and write events.

burn-sessions rows are written by the Burn API at POST /sessions:
    {session_id, agent_id, client_id, status: active|revoked, tools: [...], send_to: [...]}
Revoking a session flips status to "revoked". That flip is the kill switch:
tokens already issued to the badge stay valid at Cognito, but every call
they make is refused here.
"""
import functools
import time

import boto3
from burn_contracts.names import EVENTS_STREAM, EVENTS_TABLE, REGION, SESSIONS_TABLE

# GSIs are eventually consistent: a brand-new session can take a moment to
# appear on by_client, so a miss is retried once before the badge is refused.
_GSI_RETRY_SECONDS = 0.4

# client_id -> session_id never changes, so it is safe to cache per container.
# Status is NOT cached: it is read with a consistent read on every call.
_session_ids: dict[str, str] = {}


@functools.cache
def _dynamodb():
    return boto3.resource("dynamodb", region_name=REGION)


def _session_id_for_client(client_id: str) -> str | None:
    if client_id in _session_ids:
        return _session_ids[client_id]
    index = _dynamodb().Table(SESSIONS_TABLE)
    for attempt in range(2):
        items = index.query(
            IndexName="by_client",
            KeyConditionExpression="client_id = :c",
            ExpressionAttributeValues={":c": client_id},
        ).get("Items", [])
        if items:
            _session_ids[client_id] = items[0]["session_id"]
            return items[0]["session_id"]
        if attempt == 0:
            time.sleep(_GSI_RETRY_SECONDS)
    return None


def session_for_client(client_id: str) -> dict | None:
    """The session row for this badge, read consistently, or None if unknown."""
    if not client_id:
        return None
    session_id = _session_id_for_client(client_id)
    if session_id is None:
        return None
    response = _dynamodb().Table(SESSIONS_TABLE).get_item(
        Key={"session_id": session_id}, ConsistentRead=True
    )
    return response.get("Item")


def put_event(event: dict) -> None:
    """Store an event built by burn_contracts.events.make_event."""
    _dynamodb().Table(EVENTS_TABLE).put_item(Item={"stream": EVENTS_STREAM, **event})
