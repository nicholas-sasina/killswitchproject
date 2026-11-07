"""Fixed resource names shared by every component.

Stacks and services find each other by these names instead of passing
CloudFormation exports around, so each lane can deploy its own stack alone.
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


def is_agent_id(value: str) -> bool:
    return bool(AGENT_ID_RE.match(value or ""))


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
