"""REQUEST step: swap {{burn:ref:<secret_ref>}} for the real value in tool arguments.

Only in the arguments a tool declares in `accepts_secret_refs` (contracts/mcp-tools.json),
never for send_message or notify, and only for a secret this session may use: one of the
agent's own credentials, or one already in its ledger (it was replaced in what it read).
Never a canary, and never the agent's badge secret. The value goes to the tool, not to the
agent; if the tool echoes it, the RESPONSE step replaces it again.
"""
import functools

import boto3
from boto3.dynamodb.conditions import Key
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
    return bool(rows)


def _value(secret_ref: str) -> str:
    try:
        return _secrets().get_secret_value(SecretId=secret_ref, VersionStage="AWSCURRENT")["SecretString"]
    except _secrets().exceptions.ResourceNotFoundException:
        raise NotAllowed(f"no such credential: {secret_ref}")


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
            if not _allowed(agent_id, ref):
                raise NotAllowed(f"reference not allowed for this agent: {ref}")
        resolved[name] = REF_RE.sub(lambda m: _value(m["secret_ref"]), text)
    return resolved
