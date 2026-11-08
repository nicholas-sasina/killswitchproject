"""Issue, rotate and delete the credentials one agent owns."""
import api_key
import aws_key
import fingerprints
import flags
import registry
import shared
import vault
from burn_contracts.names import SHARED_OWNER, agent_secret_ref, owner_of

BY_TYPE = {"api_key": api_key, "aws_key": aws_key}


def issue(agent_id: str, types: list[str]) -> list[dict]:
    """Return references for the requested types, minting only what is missing."""
    unknown = set(types) - set(BY_TYPE)
    if unknown:
        raise ValueError(f"unknown credential types: {sorted(unknown)}")
    result = []
    for secret_type in types:
        row = registry.get(agent_secret_ref(agent_id, secret_type))
        if row is None:
            row = BY_TYPE[secret_type].issue(agent_id)
        result.append({"secret_ref": row["secret_ref"], "type": secret_type, "owner": "agent",
                       "version": int(row["version"]), "expires_at": None})
    return result


def rotate(agent_id: str, exposed_refs: list[str], evidence: dict | None = None) -> dict:
    """Rotate everything the agent owns; flag (at least `seen`) everything else it was exposed to.

    Canaries the agent owns are skipped: they never authenticate, so there is nothing to rotate,
    and they must keep their value so a later presentation is still attributed.
    evidence: optional safe ids (event_id, session_id) of what caused the burn.
    """
    evidence = flags.clean_evidence({**(evidence or {}), "source": "burn", "agent_id": agent_id})
    rotated = []
    for row in registry.owned_by(agent_id):
        if row["secret_type"] not in BY_TYPE:
            continue
        BY_TYPE[row["secret_type"]].rotate(row)
        rotated.append(row["secret_ref"])
    flagged, levels = [], {}
    for ref in exposed_refs:
        try:
            owner = owner_of(ref)
        except ValueError:
            continue  # not a Burn secret_ref
        if owner == agent_id:
            continue
        result = flags.raise_level(ref, "seen", evidence)
        if result:
            flagged.append(ref)
            levels[ref] = result["flag_level"]
    return {"rotated": rotated, "flagged": flagged, "flag_levels": levels}


def rotate_flagged(row: dict) -> int:
    """Rotate one flagged credential on its owner's decision. Returns the new version."""
    if row["owner_agent_id"] == SHARED_OWNER:
        return shared.rotate(row)
    if row["secret_type"] not in BY_TYPE:
        raise ValueError("this credential cannot be rotated")
    BY_TYPE[row["secret_type"]].rotate(row)
    return int(registry.get(row["secret_ref"], consistent=True)["version"])


def delete(agent_id: str) -> list[str]:
    """Delete only the credential types the agent actually has; safe to repeat.

    Skipping absent types matters for IAM: asking about a user that was never
    created is answered with AccessDenied (the minter's policy is scoped to
    /burn-agents/ users), not "no such entity".
    """
    deleted = []
    for secret_type, module in BY_TYPE.items():
        ref = agent_secret_ref(agent_id, secret_type)
        if registry.get(ref) is None and not vault.exists(ref):
            continue
        vault.delete(ref)
        module.teardown(agent_id)
        fingerprints.delete_all(ref)
        registry.delete(ref)
        deleted.append(ref)
    return deleted
