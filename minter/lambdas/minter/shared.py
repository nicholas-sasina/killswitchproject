"""Shared credentials: one value several agents may read, owned by nobody.

The value comes from a scenario template such as
'postgres://svc:{random}@db.internal.example/crm'. Burn never rotates these;
when a burned agent has seen one, it is only flagged for a human to rotate.
"""
import fingerprints
import registry
import vault
from burn_contracts.names import SHARED_OWNER, shared_secret_ref
from randomness import random_text

RANDOM_LENGTH = 24
PLACEHOLDER = "{random}"


def issue(name: str, secret_type: str, template: str) -> dict:
    secret_ref = shared_secret_ref(name)
    existing = registry.get(secret_ref)
    if existing:
        return existing
    if PLACEHOLDER not in template:
        raise ValueError("template must contain {random}")
    random_part = random_text(RANDOM_LENGTH)
    value = template.replace(PLACEHOLDER, random_part)
    vault.put(secret_ref, value)
    _register(secret_ref, secret_type, 1, value, random_part)
    # The template has no secret in it ({random} is the secret part); kept so an owner can rotate.
    return registry.put(secret_ref, SHARED_OWNER, secret_type, 1, [], template=template)


def _register(secret_ref: str, secret_type: str, version: int, value: str, random_part: str) -> None:
    fingerprints.register(secret_ref, SHARED_OWNER, secret_type, version, [
        ("value", value, True, False),
        ("random", random_part, True, True),
    ])


def rotate(row: dict) -> int:
    """Owner-requested rotation of a flagged shared credential. Returns the new version."""
    template = row.get("template")
    if not template or PLACEHOLDER not in template:
        raise ValueError("cannot rotate: issued before templates were stored; re-issue it")
    secret_ref, old_version = row["secret_ref"], int(row["version"])
    random_part = random_text(RANDOM_LENGTH)
    value = template.replace(PLACEHOLDER, random_part)
    vault.put(secret_ref, value)  # the old value is now AWSPREVIOUS
    fingerprints.mark_rotated(secret_ref, old_version)  # a leak of the old value is still caught
    version = registry.bump_version(row)
    _register(secret_ref, row["secret_type"], version, value, random_part)
    return version


def delete_all() -> list[str]:
    deleted = []
    for row in registry.owned_by(SHARED_OWNER):
        ref = row["secret_ref"]
        vault.delete(ref)
        fingerprints.delete_all(ref)
        registry.delete(ref)
        deleted.append(ref)
    return deleted
