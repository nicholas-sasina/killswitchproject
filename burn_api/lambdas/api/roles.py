"""Roles: what an instance may do. Stored roles live in burn-roles; scenario roles are read-only.

A scenario agent is a role with one instance (managed_by scenario). Those roles are built from
burn-scenario on every read and never stored, so the scenario file stays the only source.
"""
import re
from botocore.exceptions import ClientError
from burn_contracts import names
from burn_contracts.events import utc_now_iso

import db
import errors

CREDENTIAL_TYPES = ("api_key", "aws_key")
MANAGERS = ("code", "ui")
_EGRESS_RE = re.compile(r"^(\*\.)?[a-z0-9.-]{1,253}$")
_FIELDS = ("tools", "credentials", "may_message", "may_spawn", "egress", "owner")


def _now() -> str:
    return utc_now_iso()


def _bad(message: str):
    return errors.ApiError(400, message)


def _string_list(body: dict, key: str, check=None) -> list[str]:
    value = body.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(f"{key} must be a list of strings")
    if check and not all(check(v) for v in value):
        raise _bad(f"{key} has an invalid entry")
    return list(dict.fromkeys(value))


def parse_spec(body: dict) -> dict:
    """Validate a RoleSpec request body (contracts/burn-api.openapi.yaml)."""
    if not names.is_role_name(body.get("name")):
        raise _bad("name must match ^[a-z0-9][a-z0-9-]{0,31}$")
    if names.is_reserved_role_name(body["name"]):
        raise _bad("role names 'eval' and 'eval-*' are reserved for the eval harness")
    managed_by = body.get("managed_by", "ui")
    if managed_by not in MANAGERS:
        raise _bad("managed_by must be code or ui")
    if not isinstance(body.get("tools"), list):
        raise _bad("tools is required (a list of tool names)")
    egress = body.get("egress")
    if egress is not None:
        egress = _string_list(body, "egress", lambda h: bool(_EGRESS_RE.match(h)))
    owner = body.get("owner")
    if owner is not None and not isinstance(owner, str):
        raise _bad("owner must be a string")
    flags = {}
    for key in ("canaries", "auto_burn"):
        value = body.get(key, True)
        if not isinstance(value, bool):
            raise _bad(f"{key} must be true or false")
        flags[key] = value
    return {
        "name": body["name"], "managed_by": managed_by,
        "tools": _string_list(body, "tools"),
        "credentials": _string_list(body, "credentials", lambda c: c in CREDENTIAL_TYPES),
        "may_message": _string_list(body, "may_message", names.is_role_name),
        "may_spawn": _string_list(body, "may_spawn", names.is_role_name),
        "egress": egress, "owner": owner, **flags,
    }


# --- scenario roles ------------------------------------------------------------------------

def _scenario_role(agent: dict) -> dict:
    def role_name(agent_id):
        try:
            return names.scenario_role_name(agent_id)
        except ValueError:
            return None

    return {
        "name": names.scenario_role_name(agent["id"]), "tools": list(agent.get("tools", [])),
        "credentials": list(agent.get("credentials", [])),
        "may_message": [r for r in map(role_name, agent.get("send_to", [])) if r],
        "may_spawn": [], "egress": None, "owner": None, "canaries": True, "auto_burn": True,
        "managed_by": "scenario",
    }


def scenario_roles() -> dict[str, dict]:
    meta = db.table(names.SCENARIO_TABLE).get_item(Key={"pk": "meta"}).get("Item")
    roles = {}
    for agent_id in (meta["data"].get("agent_ids", []) if meta else []):
        agent = db.get_scenario_agent(agent_id)
        try:
            role = _scenario_role(agent | {"id": agent_id}) if agent else None
        except ValueError:  # agent id too long to read as a role name; it still works by agent_id
            continue
        if role:
            roles[role["name"]] = role
    return roles


# --- reads -----------------------------------------------------------------------------------

def _stored(name: str) -> dict | None:
    return db.table(names.ROLES_TABLE).get_item(Key={"name": name}).get("Item")


def lookup(name: str) -> dict:
    """The role as the instance code needs it (stored or scenario). Raises 404."""
    role = _stored(name) or scenario_roles().get(name)
    if role is None:
        raise errors.ApiError(404, "Unknown role")
    return role


def _view(role: dict, active: int) -> dict:
    # Rows stored before these fields existed mean "on", like canary.role_policy reads them.
    return {"canaries": True, "auto_burn": True, **role, "active_instances": active}


def _active_counts() -> dict[str, int]:
    import instances
    counts: dict[str, int] = {}
    for session in instances.active_sessions():
        name = instances.role_of(session)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def list_roles() -> list[dict]:
    counts = _active_counts()
    stored = db.scan_all(db.table(names.ROLES_TABLE))
    return [_view(r, counts.get(r["name"], 0)) for r in [*scenario_roles().values(), *stored]]


def get_role(name: str) -> dict:
    return _view(lookup(name), _active_counts().get(name, 0))


# --- writes ----------------------------------------------------------------------------------

def _conflict_if_scenario(name: str):
    if name in scenario_roles():
        raise errors.ApiError(409, "Scenario roles are managed by the scenario file")


def create_role(body: dict) -> dict:
    spec = parse_spec(body)
    _conflict_if_scenario(spec["name"])
    now = _now()
    try:
        db.table(names.ROLES_TABLE).put_item(
            Item={**spec, "created_at": now, "updated_at": now},
            ConditionExpression="attribute_not_exists(#n)", ExpressionAttributeNames={"#n": "name"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise errors.ApiError(409, "Role exists") from None
        raise
    return get_role(spec["name"])


def put_role(name: str, body: dict) -> dict:
    spec = parse_spec(body)
    if spec["name"] != name:
        raise _bad("body name must match the path")
    _conflict_if_scenario(name)
    existing = _stored(name)
    now = _now()
    try:
        db.table(names.ROLES_TABLE).put_item(
            Item={**spec, "created_at": existing["created_at"] if existing else now, "updated_at": now},
            # Replace only a role this caller's source owns, even if it changed since we read it.
            ConditionExpression="attribute_not_exists(#n) OR managed_by = :m",
            ExpressionAttributeNames={"#n": "name"}, ExpressionAttributeValues={":m": spec["managed_by"]},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            owner = (_stored(name) or {}).get("managed_by", "another source")
            raise errors.ApiError(409, f"Role is managed by {owner}") from None
        raise
    return get_role(name)
