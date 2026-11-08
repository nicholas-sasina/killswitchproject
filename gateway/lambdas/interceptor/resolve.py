"""REQUEST step: swap {{burn:ref:<secret_ref>}} for the real value in tool arguments.

Only in the arguments a tool declares in `accepts_secret_refs` (contracts/mcp-tools.json),
never for send_message or notify, and only for a secret this session may use:
- one of the agent's own credentials (its api_key or aws_key), or
- one the agent read itself (a ledger row that did not come from a message), and only while the
  version it read is still the current one: after a rotation, the old reference resolves to nothing.
Never a canary, and never the agent's badge secret. Any error while checking or reading refuses the
call. The value goes to the tool, not to the agent; if the tool echoes it, the RESPONSE step replaces
it again.
"""
import functools

import boto3
from boto3.dynamodb.conditions import Key

import detection
from burn_contracts import tools
from burn_contracts.names import EXPOSURES_TABLE, REGION, is_canary_ref, owner_of
from burn_contracts.references import REF_RE

OWN_SECRETS = {"api_key", "aws_key"}  # an agent's own credentials; never its badge


class NotAllowed(Exception):
    pass


@functools.cache
def _secrets():
    return boto3.client("secretsmanager", region_name=REGION)


@functools.cache
def _exposures():
    return boto3.resource("dynamodb", region_name=REGION).Table(EXPOSURES_TABLE)


def _allowed(agent_id: str, secret_ref: str) -> bool:
    if is_canary_ref(secret_ref):
        return False
    if owner_of(secret_ref) == agent_id and secret_ref.rsplit("/", 1)[-1] in OWN_SECRETS:
        return True
    rows = _exposures().query(
        KeyConditionExpression=Key("agent_id").eq(agent_id) & Key("secret_ref_or_fp").eq(secret_ref),
        ConsistentRead=True)["Items"]
    row = rows[0] if rows else None
    if not row or row.get("source") == "handoff":
        return False  # a message passes on the sender's ledger rows, not the right to use them
    # The fingerprint the agent saw must still belong to the credential's current version.
    seen = detection.lookup({row.get("fingerprint") or ""}).get(row.get("fingerprint") or "")
    return bool(seen) and seen.get("secret_ref") == secret_ref and seen.get("status") == "active"


def _checked(agent_id: str, secret_ref: str) -> bool:
    try:
        return _allowed(agent_id, secret_ref)
    except Exception as exc:  # fail closed: a check that cannot finish allows nothing
        raise NotAllowed(f"reference could not be checked: {secret_ref} ({type(exc).__name__})") from None


def _value(secret_ref: str) -> str:
    try:
        return _secrets().get_secret_value(SecretId=secret_ref, VersionStage="AWSCURRENT")["SecretString"]
    except Exception as exc:  # not found, access denied, throttled: the call is refused, never sent half-resolved
        raise NotAllowed(f"credential unavailable: {secret_ref} ({type(exc).__name__})") from None


def resolve_arguments(agent_id: str, tool: str, arguments: dict) -> dict:
    """Arguments with permitted references resolved. Raises NotAllowed for any other reference."""
    names = tools.resolvable_args(tool)
    if not names:
        return arguments
    resolved = dict(arguments)
    for name in names:
        text = resolved.get(name)
        if not isinstance(text, str) or "{{burn:ref:" not in text:
            continue
        refs = {m["secret_ref"] for m in REF_RE.finditer(text)}
        for ref in sorted(refs):
            if not _checked(agent_id, ref):
                raise NotAllowed(f"reference not allowed for this agent: {ref}")
        resolved[name] = REF_RE.sub(lambda m: _value(m["secret_ref"]), text)
    return resolved
