"""Fixed resource names shared by every component.

Stacks and services find each other by these names instead of passing
CloudFormation exports around, so each stack can be deployed on its own.
"""
import re

REGION = "us-east-1"

# DynamoDB tables (created by the BurnData stack).
EVENTS_TABLE = "burn-events"
EXPOSURES_TABLE = "burn-exposures"
CREDENTIALS_TABLE = "burn-credentials"
FINGERPRINTS_TABLE = "burn-fingerprints"
SESSIONS_TABLE = "burn-sessions"
SCENARIO_TABLE = "burn-scenario"
MESSAGES_TABLE = "burn-messages"
ROLES_TABLE = "burn-roles"
ENROLL_TOKENS_TABLE = "burn-enroll-tokens"

# Partition key value for the single global event stream in burn-events.
EVENTS_STREAM = "all"

# System secrets (created by the BurnData stack).
FINGERPRINT_KEY_SECRET = "burn-system/fingerprint-key"
DEMO_TOKEN_SECRET = "burn-system/demo-token"

# Lambda function names.
MINTER_FUNCTION = "burn-minter"

# Header the Burn API checks on every request.
DEMO_TOKEN_HEADER = "x-burn-demo-token"

# Header mock servers add so the dashboard can show a MOCK banner.
MOCK_HEADER = "x-burn-mock"

AGENT_ID_RE = re.compile(r"^agent-[a-z0-9-]{1,40}$")
SHARED_OWNER = "shared"

# Roles. A role name plus "-" and a 6-hex suffix must still fit an agent id, hence 32 chars.
ROLE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
INSTANCE_SUFFIX_RE = re.compile(r"^[0-9a-f]{6}$")
ROLE_SOURCES = ("code", "ui", "scenario")  # managed_by: burn.yaml, dashboard, scenario file

# Enrollment (join) tokens: "bjt_<token_id>.<secret>". Only sha256(secret) is stored.
JOIN_TOKEN_PREFIX = "bjt_"
JOIN_TOKEN_RE = re.compile(r"^bjt_(?P<token_id>[0-9a-f]{16})\.(?P<secret>[A-Za-z0-9_-]{43})$")


def is_agent_id(value: str) -> bool:
    return bool(AGENT_ID_RE.match(value or ""))


def is_role_name(value: str) -> bool:
    return bool(ROLE_NAME_RE.match(value or ""))


def instance_agent_id(role: str, suffix: str) -> str:
    """Agent id of one instance of a role, e.g. agent-worker-3fa91c.

    Every instance gets its own agent id, so its credentials, ledger and burn are its own.
    """
    if not is_role_name(role) or not INSTANCE_SUFFIX_RE.match(suffix or ""):
        raise ValueError(f"invalid role or suffix: {role!r} {suffix!r}")
    return f"agent-{role}-{suffix}"


def scenario_role_name(agent_id: str) -> str:
    """Role name of a scenario agent: agent-reader -> reader (a role with one instance)."""
    name = agent_id[len("agent-"):] if is_agent_id(agent_id) else ""
    if not is_role_name(name):
        raise ValueError(f"agent id has no valid role name: {agent_id!r}")
    return name


def parse_join_token(value: str) -> tuple[str, str] | None:
    """(token_id, secret) for a well-formed join token, else None."""
    match = JOIN_TOKEN_RE.match(value or "")
    return (match["token_id"], match["secret"]) if match else None


def agent_secret_ref(agent_id: str, name: str) -> str:
    """Secrets Manager name of an agent's own credential, e.g. burn/agent-reader/api_key."""
    if not is_agent_id(agent_id):
        raise ValueError(f"invalid agent id: {agent_id!r}")
    return f"burn/{agent_id}/{name}"


def shared_secret_ref(name: str) -> str:
    return f"burn/{SHARED_OWNER}/{name}"


def owner_of(secret_ref: str) -> str:
    """'shared' or the owning agent id, parsed from a secret_ref."""
    parts = secret_ref.split("/")
    if len(parts) != 3 or parts[0] != "burn":
        raise ValueError(f"not a burn secret_ref: {secret_ref!r}")
    return parts[1]
