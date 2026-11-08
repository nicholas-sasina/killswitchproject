"""POST /sessions: start an agent session (badge + credentials) and revoke it later.

Order matters: credentials first (idempotent, safe to repeat), then the badge, then
the session row. A session row only exists once everything it needs exists.
"""
import json
import secrets
from datetime import datetime, timezone

import badges  # bundled from the identity code at deploy time
import boto3
from burn_contracts import names

import db
import minter_client

SCOPE = "burn/tools"


class NotFound(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def revoke_sessions(agent_id: str) -> list[str]:
    """Mark every active session of the agent revoked and delete its Cognito client.

    The status flip is the real kill switch (the gateway checks it on every call).
    Deleting the client only stops new tokens being issued.
    """
    revoked = []
    for session in db.sessions_for_agent(agent_id):
        if session.get("status") != "active":
            continue
        db.table(names.SESSIONS_TABLE).update_item(
            Key={"session_id": session["session_id"]},
            UpdateExpression="SET #s = :r, revoked_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":r": "revoked", ":t": _now()},
        )
        try:
            badges.delete_badge(session["client_id"])
        except Exception as exc:  # already revoked in DynamoDB; a leftover client is harmless
            print(f"delete_badge failed for {session['session_id']}: {type(exc).__name__}")
        revoked.append(session["session_id"])
    return revoked


def _store_badge(agent_id: str, badge: dict) -> str:
    ref = names.agent_secret_ref(agent_id, "badge")
    client = boto3.client("secretsmanager", region_name=names.REGION)
    value = json.dumps(badge)
    try:
        client.put_secret_value(SecretId=ref, SecretString=value)
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(Name=ref, SecretString=value)
    return ref


def start_session(agent_id: str, owner_sub: str, owner_name: str) -> dict:
    agent = db.get_agent(agent_id)
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
        "owner_sub": owner_sub,
        "owner_name": owner_name,
        "client_id": badge["client_id"],
        "status": "active",
        "tools": list(agent.get("tools", [])),
        "send_to": list(agent.get("send_to", [])),
        "created_at": _now(),
    })
    db.write_event(
        agent_id=agent_id, source="session", action="issued", session_id=session_id,
        detail=f"session started by {owner_name} ({owner_sub}) with {len(credentials)} credentials",
    )
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "owner_sub": owner_sub,
        "owner_name": owner_name,
        "badge_ref": badge_ref,
        "credentials": credentials,
    }
