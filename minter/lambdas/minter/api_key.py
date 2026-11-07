"""Per-agent API keys for the mock provider: mpk_live_ + 32 random chars."""
import aws
import fingerprints
import registry
import vault
from burn_contracts.names import agent_secret_ref
from randomness import random_text

PREFIX = "mpk_live_"
BODY_LENGTH = 32


def _register(secret_ref: str, owner: str, value: str, version: int) -> None:
    # Whole key for exact matches; the random body as shingles so fragments
    # and re-cased copies are still recognised. The public prefix is not secret.
    fingerprints.register(secret_ref, owner, "api_key", version, [
        ("value", value, True, False),
        ("random", value[len(PREFIX):], False, True),
    ])


def issue(agent_id: str) -> dict:
    secret_ref = agent_secret_ref(agent_id, "api_key")
    value = PREFIX + random_text(BODY_LENGTH)
    vault.put(secret_ref, value)
    _register(secret_ref, agent_id, value, 1)
    return registry.put(secret_ref, agent_id, "api_key", 1, [aws.setting("PROVIDER_HOST")])


def rotate(row: dict) -> None:
    secret_ref, owner, old_version = row["secret_ref"], row["owner_agent_id"], int(row["version"])
    value = PREFIX + random_text(BODY_LENGTH)
    vault.put(secret_ref, value)  # the old value is now AWSPREVIOUS
    fingerprints.mark_rotated(secret_ref, old_version)
    _register(secret_ref, owner, value, registry.bump_version(row))


def teardown(agent_id: str) -> None:
    """Nothing outside Secrets Manager and the tables to clean up."""
