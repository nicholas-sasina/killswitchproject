"""Canaries: one decoy credential per agent session (or instance).

A canary looks exactly like a mock-provider api_key (mpk_live_ + 32 random
chars) but can never authenticate. It is planted where only its own session
reads it (the Gateway's {{canary}} placeholder). Reading it is just an exposure;
presenting it to the provider is the incident, and the fingerprint says which
session it came from, however the value got out.

Where things live (contracts/README.md):
- the value: Secrets Manager at names.canary_secret_ref(agent_id, session_id), only;
- burn-credentials: one row per canary, secret_type "canary", with session_id,
  instance_id, status (issuing -> active -> tripped) and issued_at;
- burn-fingerprints: exact + shingle rows with secret_type "other" and canary=true,
  so detectors record it like any registered credential and no provider build
  can ever accept it as an api_key.

Issuing is idempotent per session: the registry row is claimed with a conditional
put, and the secret is created but never overwritten, so retries and concurrent
calls for the same session all end up with the same single canary.
Canaries are not deleted when a session ends or is burned (they stay attributable);
only an explicit reset deletes them.
"""
from burn_contracts import fingerprint as fp
from burn_contracts.events import utc_now_iso
from burn_contracts.names import SESSION_ID_RE, canary_secret_ref, is_agent_id

import api_key
import fingerprints
import registry
import vault
from randomness import random_text

SECRET_TYPE = "canary"
FINGERPRINT_TYPE = "other"  # see module docstring: never "api_key"
ISSUING, ACTIVE = "issuing", "active"
MAX_FRESH_ATTEMPTS = 5


class NotFound(Exception):
    pass


def _check_ids(agent_id: str, session_id: str, instance_id: str | None) -> str:
    if not is_agent_id(agent_id):
        raise ValueError(f"invalid agent id: {agent_id!r}")
    if instance_id is not None and not SESSION_ID_RE.match(instance_id):
        raise ValueError("invalid instance id")
    return canary_secret_ref(agent_id, session_id)  # validates session_id


def metadata(row: dict) -> dict:
    """What callers get back: references and ids, never the value."""
    return {
        "canary_ref": row["secret_ref"], "agent_id": row["owner_agent_id"],
        "session_id": row["session_id"], "instance_id": row.get("instance_id"),
        "version": int(row["version"]), "status": row["status"], "issued_at": row["issued_at"],
    }


def _claim(secret_ref: str, agent_id: str, session_id: str, instance_id: str | None) -> dict:
    row = {
        "secret_ref": secret_ref, "owner_agent_id": agent_id, "secret_type": SECRET_TYPE,
        "session_id": session_id, "version": 1, "status": ISSUING, "issued_at": utc_now_iso(),
        "allowed_hosts": [],  # a canary is welcome nowhere
    }
    if instance_id is not None:
        row["instance_id"] = instance_id
    if registry.put_if_absent(row):
        return row
    return registry.get(secret_ref, consistent=True)  # someone else claimed it first


def _same_holder(row: dict, agent_id: str, session_id: str, instance_id: str | None) -> None:
    if row.get("secret_type") != SECRET_TYPE:
        raise ValueError("secret_ref is registered to something that is not a canary")
    if row["owner_agent_id"] != agent_id or row["session_id"] != session_id:
        raise ValueError("canary name already belongs to a different session")
    if instance_id is not None and row.get("instance_id") not in (None, instance_id):
        raise ValueError("canary already issued to a different instance of this session")


def _fresh_value() -> str:
    """A new canary whose fingerprint is not registered to anything else."""
    hmac_key = fingerprints.key()
    for _ in range(MAX_FRESH_ATTEMPTS):
        value = api_key.PREFIX + random_text(api_key.BODY_LENGTH)
        if fingerprints.get(fp.fingerprint(value, hmac_key)) is None:
            return value
    raise RuntimeError("could not draw an unused canary value")  # ~190 random bits: unreachable


def _register(row: dict, value: str) -> None:
    extra = {"canary": True, "session_id": row["session_id"]}
    if row.get("instance_id") is not None:
        extra["instance_id"] = row["instance_id"]
    # Same parts as a real api_key: the whole value for exact matches, the random
    # body as shingles so fragments of a leaked canary are still recognised.
    fingerprints.register(row["secret_ref"], row["owner_agent_id"], FINGERPRINT_TYPE, 1, [
        ("value", value, True, False),
        ("random", value[len(api_key.PREFIX):], False, True),
    ], extra=extra)


def _activate(secret_ref: str) -> dict:
    try:
        return registry.update_status(secret_ref, ISSUING, ACTIVE)
    except registry.conditional_failed():
        return registry.get(secret_ref, consistent=True)  # a concurrent call finished first


def issue(agent_id: str, session_id: str, instance_id: str | None = None) -> dict:
    """Issue the session's canary, or return the one it already has."""
    secret_ref = _check_ids(agent_id, session_id, instance_id)
    row = registry.get(secret_ref, consistent=True) or _claim(secret_ref, agent_id, session_id, instance_id)
    _same_holder(row, agent_id, session_id, instance_id)
    if row["status"] != ISSUING:
        return metadata(row)
    # Finish (or resume) issuing. create_only never replaces a value another call stored,
    # and re-registering the same value rewrites identical fingerprint rows.
    value = vault.create_only(secret_ref, _fresh_value())
    _register(row, value)
    return metadata(_activate(secret_ref))


def get(agent_id: str, session_id: str) -> dict:
    row = registry.get(_check_ids(agent_id, session_id, None), consistent=True)
    if row is None or row.get("secret_type") != SECRET_TYPE:
        raise NotFound("no canary issued for this session")
    return metadata(row)


def delete_for_agent(agent_id: str) -> list[str]:
    """Demo reset only: remove every canary of the agent (value, fingerprints, registry row)."""
    if not is_agent_id(agent_id):
        raise ValueError(f"invalid agent id: {agent_id!r}")
    deleted = []
    for row in registry.owned_by(agent_id):
        if row.get("secret_type") != SECRET_TYPE:
            continue
        ref = row["secret_ref"]
        if vault.exists(ref):
            vault.delete(ref)
        fingerprints.delete_all(ref)
        registry.delete(ref)
        deleted.append(ref)
    return deleted
