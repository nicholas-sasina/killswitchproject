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
import errors
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


def _narrow(mine, parents):
    """A child's list is its role's list limited to what the parent has (None = no limit)."""
    if mine is None or parents is None:
        return parents if mine is None else mine
    return [item for item in mine if item in parents]


def _spawn_limits(role_name: str, role: dict, parent_session: str) -> dict:
    """Tools, credentials and egress for a child: the role's, intersected with the parent's."""
    import instances
    import roles

    parent = instances.get_session_or_none(parent_session)
    if parent is None:
        raise errors.ApiError(404, "Unknown parent session")
    if parent["status"] != "active":
        raise errors.ApiError(403, "Parent session is not active")
    parent_role_name = instances.role_of(parent)
    parent_role = roles.lookup(parent_role_name) if parent_role_name else {"may_spawn": []}
    if role_name not in (parent_role.get("may_spawn") or []):
        raise errors.ApiError(403, f"Role {parent_role_name} may not spawn {role_name}")
    if "credentials" in parent:
        parent_credentials = list(parent["credentials"])
    else:  # a scenario parent: its credentials are the scenario agent's
        parent_credentials = list((db.get_scenario_agent(parent["agent_id"]) or {}).get("credentials", []))
    return {
        "tools": _narrow(list(role["tools"]), list(parent.get("tools", []))),
        "credentials": _narrow(list(role.get("credentials") or []), parent_credentials),
        "egress": _narrow(role.get("egress"), parent.get("egress")),
    }


def _new_instance_agent_id(role_name: str) -> str:
    for _ in range(5):
        agent_id = names.instance_agent_id(role_name, secrets.token_hex(3))
        if not db.sessions_for_agent(agent_id):
            return agent_id
    raise errors.ApiError(503, "could not pick a free instance id; retry")


def start_instance(role_name: str, enroll_token: str | None = None, parent_session: str | None = None) -> dict:
    """POST /sessions {role, enroll_token} or {role, parent_session}: start one instance of a role."""
    import roles
    import tokens

    if not names.is_role_name(role_name):
        raise errors.ApiError(400, "bad role name")
    if (enroll_token is None) == (parent_session is None):
        raise errors.ApiError(400, "send exactly one of enroll_token and parent_session")
    role = roles.lookup(role_name)
    if role["managed_by"] == "scenario":
        raise errors.ApiError(403, "Scenario roles start by agent_id")

    token_id = None
    if parent_session is not None:
        limits = _spawn_limits(role_name, role, parent_session)
    else:
        token_id = tokens.redeem(role_name, enroll_token)
        limits = {"tools": list(role["tools"]), "credentials": list(role.get("credentials") or []),
                  "egress": role.get("egress")}
    try:
        return _create_instance(role_name, role, limits, token_id, parent_session)
    except BaseException:
        if token_id:  # the join failed; the token keeps its use
            tokens.refund(token_id)
        raise


def _create_instance(role_name, role, limits, token_id, parent_session) -> dict:
    agent_id = _new_instance_agent_id(role_name)
    minted, badge = False, None
    try:
        credentials = []
        if limits["credentials"]:
            minted = True  # even a failed call may have created the IAM user before failing
            credentials = minter_client.call(
                "POST", f"/agents/{agent_id}/credentials", {"types": limits["credentials"]})["credentials"]

        session_id = "sess_" + secrets.token_hex(8)
        badge = badges.create_badge(agent_id, session_id)
        badge_ref = _store_badge(agent_id, badge)

        now = _now()
        db.table(names.SESSIONS_TABLE).put_item(Item={
            "session_id": session_id, "agent_id": agent_id, "client_id": badge["client_id"],
            "status": "active", "tools": limits["tools"], "send_to": [], "created_at": now,
            "role": role_name, "parent_session": parent_session, "token_id": token_id,
            "owner": role.get("owner"), "egress": limits["egress"], "credentials": limits["credentials"],
            "last_seen": now, "exited_at": None,
        })
    except BaseException:
        _discard_partial_instance(agent_id, minted, badge)
        raise
    fields = {"session_id": session_id, "role": role_name, "parent_session": parent_session}
    db.write_event(agent_id=agent_id, source="session", action="issued", **fields,
                   detail=f"session started with {len(credentials)} credentials")
    db.write_event(agent_id=agent_id, source="instance", action="started", **fields,
                   detail="instance started")
    return {
        "session_id": session_id, "agent_id": agent_id, "badge_ref": badge_ref, "credentials": credentials,
        "role": role_name, "parent_session": parent_session, "token_id": token_id,
        "tools": limits["tools"], "egress": limits["egress"],
    }


def _discard_partial_instance(agent_id: str, minted: bool, badge: dict | None):
    """A join failed before its session row existed: remove what it created, best effort.

    Without this every client retry (for example after a Cognito throttle) would leave another
    IAM user, key and secret behind with no session pointing at them.
    """
    if badge:
        try:
            badges.delete_badge(badge["client_id"])
        except Exception as exc:
            print(f"cleanup: delete_badge failed for {agent_id}: {type(exc).__name__}")
    if minted:
        try:
            minter_client.call("DELETE", f"/agents/{agent_id}/credentials")
        except Exception as exc:
            print(f"cleanup: credential delete failed for {agent_id}: {type(exc).__name__}")
