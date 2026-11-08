"""POST /sessions: start an agent session (badge + credentials) and revoke it later.

Order matters: credentials first (idempotent, safe to repeat), then the badge, then
the session row. A session row only exists once everything it needs exists.
"""
import hashlib
import json
import secrets
import time

import badges  # bundled from the identity code at deploy time
import boto3
from botocore.exceptions import ClientError
from burn_contracts import names
from burn_contracts.events import utc_now_iso

import caller
import canary
import db
import errors
import minter_client

SCOPE = "burn/tools"


class NotFound(Exception):
    pass


def _now() -> str:
    # Same format as heartbeat and the idle sweep (Z, milliseconds), so string comparisons hold.
    return utc_now_iso()


def end_session(session: dict, status: str = "revoked", idle_before: str | None = None) -> bool:
    """Flip an active session to `revoked` (burned) or `exited` and delete its Cognito client.

    The status flip is the real kill switch (the gateway checks it on every call).
    Deleting the client only stops new tokens being issued. Returns False if the session
    was no longer active (someone else ended it first), so an end is never recorded twice.
    With `idle_before`, the session also has to still be unseen since then (`last_seen` older), so
    a heartbeat that lands first wins over the idle sweep.
    """
    stamp = "revoked_at" if status == "revoked" else "exited_at"
    try:
        db.table(names.SESSIONS_TABLE).update_item(
            Key={"session_id": session["session_id"]},
            UpdateExpression=f"SET #s = :new, {stamp} = :t",
            ConditionExpression="#s = :active" + (" AND last_seen < :cutoff" if idle_before else ""),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":new": status, ":active": "active", ":t": _now(),
                                       **({":cutoff": idle_before} if idle_before else {})},
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
    try:
        role_name = names.scenario_role_name(agent_id)
    except ValueError:  # an agent id too long to read as a role name: default canary policy
        role_name = None
    canary.issue_for_session(agent_id, session_id, role_name)
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
        "created_by": caller.get().person,
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
        # The person behind the instance: whoever is signed in, else whoever made its token or its parent.
        # Inside the try: the token use is already spent, so a failed lookup must give it back too.
        limits["created_by"] = caller.get().person or _inherited_creator(token_id, parent_session)
        return _create_instance(role_name, role, limits, token_id, parent_session)
    except BaseException:
        if token_id:  # the join failed; the token keeps its use
            tokens.refund(token_id)
        raise


def _inherited_creator(token_id: str | None, parent_session: str | None) -> str | None:
    import instances
    import tokens
    if token_id:
        return tokens.get(token_id).get("created_by")
    parent = instances.get_session_or_none(parent_session) if parent_session else None
    return (parent or {}).get("created_by")


def _create_instance(role_name, role, limits, token_id, parent_session) -> dict:
    agent_id = _new_instance_agent_id(role_name)
    minted, badge, session_id = False, None, None
    try:
        credentials = []
        if limits["credentials"]:
            minted = True  # even a failed call may have created the IAM user before failing
            credentials = minter_client.call(
                "POST", f"/agents/{agent_id}/credentials", {"types": limits["credentials"]})["credentials"]

        session_id = "sess_" + secrets.token_hex(8)
        canary.issue_for_session(agent_id, session_id, role_name)  # None when the role has canaries off
        badge = badges.create_badge(agent_id, session_id)
        badge_ref = _store_badge(agent_id, badge)
        # Read inside the try: if the values cannot be read the instance has nothing to run with,
        # so the join fails and is cleaned up like any other failed join.
        own_secrets = _own_secrets(badge, credentials)
        # The supervisor's credential for this instance's own calls (heartbeat, exit, spawn). Only its hash is kept.
        own_secrets["session_token"] = names.SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)

        now = _now()
        db.table(names.SESSIONS_TABLE).put_item(Item={
            "session_id": session_id, "agent_id": agent_id, "client_id": badge["client_id"],
            "status": "active", "tools": limits["tools"], "send_to": [], "created_at": now,
            "role": role_name, "parent_session": parent_session, "token_id": token_id,
            "owner": role.get("owner"), "egress": limits["egress"], "credentials": limits["credentials"],
            "last_seen": now, "exited_at": None,
            "session_token_sha256": session_token_hash(own_secrets["session_token"]),
            # Only when the role sets it: an absent field means the Gateway's default mode applies.
            **({"references": role["references"]} if role.get("references") else {}),
            "created_by": limits.get("created_by"),
        })
    except BaseException:
        _discard_partial_instance(agent_id, minted, badge, session_id)
        raise
    _recheck_after_write(agent_id, session_id, badge, token_id, parent_session)
    # The row exists and the token use is spent. A failed event write must not look like a failed
    # join (the caller would refund the use), so events are best effort from here on.
    # A join has no caller of its own yet: its events are the new instance's.
    fields = {"session_id": session_id, "role": role_name, "parent_session": parent_session,
              "actor": caller.get().actor or f"instance:{session_id}"}
    for source, action, detail in (("session", "issued", f"session started with {len(credentials)} credentials"),
                                   ("instance", "started", "instance started")):
        try:
            db.write_event(agent_id=agent_id, source=source, action=action, **fields, detail=detail)
        except Exception as exc:
            print(f"event {source}/{action} not written for {session_id}: {type(exc).__name__}")
    return {
        "session_id": session_id, "agent_id": agent_id, "badge_ref": badge_ref, "credentials": credentials,
        "role": role_name, "parent_session": parent_session, "token_id": token_id,
        "tools": limits["tools"], "egress": limits["egress"], "created_by": limits.get("created_by"),
        "secrets": own_secrets,  # returned once; never logged, stored or sent again
    }


def session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


SECRET_READ_ATTEMPTS = 4


def _read_secret(client, ref: str) -> str:
    """GetSecretValue with a short backoff: a secret the minter just wrote may not be readable yet."""
    for attempt in range(SECRET_READ_ATTEMPTS):
        try:
            return client.get_secret_value(SecretId=ref)["SecretString"]
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            retryable = code == "ResourceNotFoundException" or code in errors.THROTTLE_CODES
            if not retryable or attempt == SECRET_READ_ATTEMPTS - 1:
                raise
            time.sleep(0.2 * 2 ** attempt)


def _own_secrets(badge: dict, credentials: list[dict]) -> dict:
    """The instance's own secret values for the join reply: badge plus the api_key / aws_key minted.

    Only these two credential types are read, and only the grant on burn/agent-*/api_key and
    aws_key allows it. The values are returned to the caller and not printed or stored here.
    """
    out = {"badge": {k: badge[k] for k in ("client_id", "client_secret", "token_url", "scope")}}
    client = boto3.client("secretsmanager", region_name=names.REGION)
    for cred in credentials:
        if cred.get("type") == "api_key":
            out["api_key"] = _read_secret(client, cred["secret_ref"])
        elif cred.get("type") == "aws_key":
            raw = json.loads(_read_secret(client, cred["secret_ref"]))
            out["aws_key"] = {"access_key_id": raw["access_key_id"], "secret_access_key": raw["secret_access_key"]}
    return out


def _recheck_after_write(agent_id, session_id, badge, token_id, parent_session):
    """Refuse a child whose parent was burned (or whose token was revoked) while it was starting.

    A scoped burn scans for targets and then revokes them. A child written after that scan would
    escape it, so the child looks again after its own row exists: either the burn's next scan
    sees the row, or this check sees the revoked parent or token. Reads are strongly consistent.
    """
    import instances
    import tokens

    if parent_session is not None:
        parent = instances.get_session_or_none(parent_session, consistent=True)
        allowed = parent is not None and parent.get("status") == "active"
    else:
        allowed = not tokens.get(token_id, consistent=True)["revoked"]
    if allowed:
        return
    end_session({"session_id": session_id, "client_id": badge["client_id"]}, "revoked")
    try:
        minter_client.call("DELETE", f"/agents/{agent_id}/credentials")
    except Exception as exc:
        print(f"cleanup: credential delete failed for {agent_id}: {type(exc).__name__}")
    raise errors.ApiError(403, "Parent session is not active" if parent_session else "Join token revoked")


def _discard_partial_instance(agent_id: str, minted: bool, badge: dict | None, session_id: str | None = None):
    """A join failed before its session row existed: remove what it created, best effort.

    No session row means reset can never find this agent (instance ids are unique), so everything
    created for it goes now: the badge client and secret, the canary (issued once the session id
    exists) and the credentials. Without this every client retry, for example after a Cognito
    throttle, would leave another IAM user, key, secret and canary behind.
    """
    if badge:
        try:
            badges.delete_badge(badge["client_id"])
        except Exception as exc:
            print(f"cleanup: delete_badge failed for {agent_id}: {type(exc).__name__}")
        try:  # it may not have been written yet (a no-op then); a missing permission is not fatal
            boto3.client("secretsmanager", region_name=names.REGION).delete_secret(
                SecretId=names.agent_secret_ref(agent_id, "badge"), ForceDeleteWithoutRecovery=True)
        except Exception as exc:
            print(f"cleanup: badge secret delete failed for {agent_id}: {type(exc).__name__}")
    if session_id:
        try:
            minter_client.call("DELETE", f"/agents/{agent_id}/canaries")
        except Exception as exc:
            print(f"cleanup: canary delete failed for {agent_id}: {type(exc).__name__}")
    if minted:
        try:
            minter_client.call("DELETE", f"/agents/{agent_id}/credentials")
        except Exception as exc:
            print(f"cleanup: credential delete failed for {agent_id}: {type(exc).__name__}")
