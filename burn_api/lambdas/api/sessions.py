"""POST /sessions: start an agent session (badge + credentials) and revoke it later.

Order matters: credentials first (idempotent, safe to repeat), then the badge, then
the session row. A session row only exists once everything it needs exists.
"""
import json
import secrets
from datetime import datetime, timezone

import badges  # bundled from the identity code at deploy time
import boto3
from botocore.exceptions import ClientError
from burn_contracts import names

import db
import minter_client

SCOPE = "burn/tools"


class NotFound(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def end_session(session: dict, status: str = "revoked") -> bool:
    """Flip an active session to `revoked` (burned) or `exited` and delete its Cognito client.

    The status flip is the real kill switch (the gateway checks it on every call).
    Deleting the client only stops new tokens being issued. Returns False if the session
    was no longer active (someone else ended it first), so an end is never recorded twice.
    """
    stamp = "revoked_at" if status == "revoked" else "exited_at"
    try:
        db.table(names.SESSIONS_TABLE).update_item(
            Key={"session_id": session["session_id"]},
            UpdateExpression=f"SET #s = :new, {stamp} = :t",
            ConditionExpression="#s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":new": status, ":active": "active", ":t": _now()},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    try:
        badges.delete_badge(session["client_id"])
    except Exception as exc:  # already ended in DynamoDB; a leftover client is harmless
        print(f"delete_badge failed for {session['session_id']}: {type(exc).__name__}")
    return True


def revoke_sessions(agent_id: str) -> list[str]:
    """Mark every active session of the agent revoked and delete its Cognito client."""
    return [s["session_id"] for s in db.sessions_for_agent(agent_id)
            if s.get("status") == "active" and end_session(s, "revoked")]


def _store_badge(agent_id: str, badge: dict) -> str:
    ref = names.agent_secret_ref(agent_id, "badge")
    client = boto3.client("secretsmanager", region_name=names.REGION)
    value = json.dumps(badge)
    try:
        client.put_secret_value(SecretId=ref, SecretString=value)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=ref, SecretString=value)
    return ref


def start_session(agent_id: str) -> dict:
    # Scenario agents only: an instance's agent id is not a way to start a session.
    agent = db.get_scenario_agent(agent_id)
    if agent is None:
        raise NotFound(agent_id)

    # One active session per agent keeps the dashboard simple.
    revoke_sessions(agent_id)

    credentials = []
    if agent.get("credentials"):
        result = minter_client.call(
            "POST", f"/agents/{agent_id}/credentials", {"types": list(agent["credentials"])}
        )
        credentials = result["credentials"]

    session_id = "sess_" + secrets.token_hex(8)
    badge = badges.create_badge(agent_id, session_id)
    badge_ref = _store_badge(agent_id, badge)

    db.table(names.SESSIONS_TABLE).put_item(Item={
        "session_id": session_id,
        "agent_id": agent_id,
        "client_id": badge["client_id"],
        "status": "active",
        "tools": list(agent.get("tools", [])),
        "send_to": list(agent.get("send_to", [])),
        "created_at": _now(),
    })
    db.write_event(
        agent_id=agent_id, source="session", action="issued", session_id=session_id,
        detail=f"session started with {len(credentials)} credentials",
    )
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "badge_ref": badge_ref,
        "credentials": credentials,
    }
