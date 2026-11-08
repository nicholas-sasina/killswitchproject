"""Fixed resource names shared by every component.

Stacks and services find each other by these names instead of passing
CloudFormation exports around, so each stack can be deployed on its own.
"""
import hashlib
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

# burn-fingerprints item the minter bumps whenever it registers, rotates or deletes fingerprints.
# Not 64 hex chars, so it can never collide with an HMAC.
FINGERPRINTS_VERSION_KEY = "_version"
# burn-exposures index for "who read this source": PK source_ref, SK first_seen.
EXPOSURES_BY_SOURCE_INDEX = "by_source"

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
# `eval` and `eval-*` are reserved: an instance of such a role would be agent-eval-*, which reset
# and the badge cleanup treat as the eval harness's load-test agents and never touch.
RESERVED_ROLE_PREFIX = "eval"
INSTANCE_SUFFIX_RE = re.compile(r"^[0-9a-f]{6}$")
ROLE_SOURCES = ("code", "ui", "scenario")  # managed_by: burn.yaml, dashboard, scenario file

# Enrollment (join) tokens: "bjt_<token_id>.<secret>". Only sha256(secret) is stored.
JOIN_TOKEN_PREFIX = "bjt_"
JOIN_TOKEN_RE = re.compile(r"^bjt_(?P<token_id>[0-9a-f]{16})\.(?P<secret>[A-Za-z0-9_-]{43})$")

# Session tokens: "bst_<secret>", issued once to a role instance's supervisor in its join reply. They authenticate
# that instance's own calls (heartbeat, exit, spawning a child). Only sha256(token) is stored.
SESSION_TOKEN_PREFIX = "bst_"
SESSION_TOKEN_RE = re.compile(r"^bst_[A-Za-z0-9_-]{43}$")


def is_agent_id(value: str) -> bool:
    return bool(AGENT_ID_RE.match(value or ""))


def is_role_name(value: str) -> bool:
    return bool(ROLE_NAME_RE.match(value or ""))


def is_reserved_role_name(value: str) -> bool:
    return value == RESERVED_ROLE_PREFIX or (value or "").startswith(RESERVED_ROLE_PREFIX + "-")


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


def is_session_token(value) -> bool:
    # fullmatch: "$" alone would also accept a trailing newline.
    return isinstance(value, str) and bool(SESSION_TOKEN_RE.fullmatch(value))


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


# Canaries: one decoy credential per agent session. It looks like a mock-provider
# api_key but never authenticates; presenting it is the signal. The secret name is
# derived from the session id so every component finds the same canary without a
# lookup, and the name stays inside the secret_ref grammar whatever the id looks like.
CANARY_PREFIX = "canary_"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

# Lambda the mock provider hands a canary incident to (direct async invoke).
BURN_API_FUNCTION = "burn-api"


def canary_secret_ref(agent_id: str, session_id: str) -> str:
    """burn/<agent_id>/canary_<24 hex of sha256(session_id)>, e.g. for the Gateway's {{canary}}."""
    if not SESSION_ID_RE.match(session_id or ""):
        raise ValueError("invalid session id")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return agent_secret_ref(agent_id, CANARY_PREFIX + digest)


def is_canary_ref(secret_ref: str) -> bool:
    parts = (secret_ref or "").split("/")
    return len(parts) == 3 and parts[0] == "burn" and parts[2].startswith(CANARY_PREFIX)
