"""Fill {{cred:...}} placeholders in scenario text at read time.

Scenario files never hold credential values. When a tool returns a file or
customer note, each placeholder is replaced with the CURRENT value from
Secrets Manager. So the raw value exists only in Secrets Manager, and after a
rotation the same file shows the new value. Formats (contracts/README.md):
    {{cred:<agent_id>:api_key}}
    {{cred:<agent_id>:aws_key.access_key_id}}   (JSON field of the secret)
    {{cred:shared:<name>}}
"""
import functools
import json
import logging
import re

import boto3
from burn_contracts.names import REGION

PLACEHOLDER = re.compile(
    r"\{\{cred:(?P<owner>agent-[a-z0-9-]{1,40}|shared):(?P<name>[a-z0-9_]{1,40})(?:\.(?P<field>[a-z_]{1,40}))?\}\}"
)
# An agent's own secrets that may appear in files. Its badge secret never may.
AGENT_SECRET_NAMES = {"api_key", "aws_key"}
UNAVAILABLE = "(credential not issued)"

log = logging.getLogger()


@functools.cache
def _secrets():
    return boto3.client("secretsmanager", region_name=REGION)


def _current_value(secret_ref: str) -> str | None:
    try:
        return _secrets().get_secret_value(SecretId=secret_ref, VersionStage="AWSCURRENT")["SecretString"]
    except _secrets().exceptions.ResourceNotFoundException:
        log.warning("placeholder secret not found: %s", secret_ref)  # the name, never a value
        return None


def resolve(text: str) -> str:
    """Return text with every placeholder replaced by its current value."""
    cache: dict[str, str | None] = {}  # one fetch per secret per call; never kept across calls

    def replace(match: re.Match) -> str:
        owner, name, field = match["owner"], match["name"], match["field"]
        if owner != "shared" and name not in AGENT_SECRET_NAMES:
            return UNAVAILABLE
        secret_ref = f"burn/{owner}/{name}"
        if secret_ref not in cache:
            cache[secret_ref] = _current_value(secret_ref)
        value = cache[secret_ref]
        if value is None:
            return UNAVAILABLE
        if field:
            try:
                value = json.loads(value).get(field)
            except (ValueError, AttributeError):
                value = None
        return value if isinstance(value, str) else UNAVAILABLE

    return PLACEHOLDER.sub(replace, text or "")
