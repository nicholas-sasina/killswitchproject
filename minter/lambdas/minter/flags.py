"""Flag levels: how worried to be about a credential an agent saw but does not own.

    seen       it entered an agent's context; no sign it was sent anywhere
    attempted  an exit check or tool-argument check blocked it from leaving
    reported   trusted evidence it was presented at a monitored service

Levels only go up. Flagging never rotates anything: shared credentials are
rotated by their human owner after review. State lives on the credential's
burn-credentials row:

    status = "flagged"                       (unchanged meaning for older readers)
    flag_level, flag_rank (1..3)             current level; the rank makes "only go up"
                                             a single conditional update
    flag_evidence_<level>                    the FIRST evidence that reached each level
    flagged_at, flagged_by, flag_updated_at

At most three evidence attributes per credential, so duplicate or repeated
events never grow the row; re-sending the same event changes nothing.
"""
import re

from burn_contracts.events import FLAG_LEVELS, utc_now_iso
from burn_contracts.names import CREDENTIALS_TABLE, SESSION_ID_RE, is_canary_ref

import aws
import registry

RANK = {level: rank for rank, level in enumerate(FLAG_LEVELS, start=1)}

# Which event sources may vouch for which level.
SOURCES_FOR = {
    "seen": {"tool_response", "handoff", "burn"},
    "attempted": {"egress", "gateway", "handoff"},
    "reported": {"provider"},
}
MAX_REFS_PER_CALL = 100

_EVIDENCE_FIELDS = {
    "source": re.compile(r"^[a-z_]{1,32}$"),
    "event_id": re.compile(r"^evt_[0-9A-HJKMNP-TV-Z]{26}$"),
    "agent_id": re.compile(r"^(agent-[a-z0-9-]{1,40}|system)$"),
    "session_id": SESSION_ID_RE,
    "tool": re.compile(r"^[A-Za-z0-9_.-]{1,64}$"),
    "ts": re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"),
    "fingerprint": re.compile(r"^[0-9a-f]{64}$"),
}


def clean_evidence(evidence) -> dict:
    """Keep only known, well-formed, non-secret fields. Raises ValueError otherwise."""
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    unknown = set(evidence) - set(_EVIDENCE_FIELDS)
    if unknown:
        raise ValueError(f"unknown evidence fields: {sorted(unknown)}")
    clean = {}
    for name, pattern in _EVIDENCE_FIELDS.items():
        value = evidence.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not pattern.match(value):
            raise ValueError(f"invalid evidence field: {name}")
        clean[name] = value
    if "source" not in clean:
        raise ValueError("evidence.source is required")
    return clean


def _check(level: str, evidence: dict) -> None:
    if level not in RANK:
        raise ValueError(f"invalid flag level: {level!r}")
    if evidence["source"] not in SOURCES_FOR[level]:
        raise ValueError(f"source {evidence['source']!r} cannot vouch for level {level!r}")


def _not_canary() -> str:
    return "(attribute_not_exists(secret_type) OR secret_type <> :canary)"


def _raise(secret_ref: str, level: str, evidence: dict) -> bool:
    """One conditional update: raise the level only if it is currently lower. True if raised."""
    condition = f"attribute_exists(secret_ref) AND {_not_canary()} AND " \
                "(attribute_not_exists(flag_rank) OR flag_rank < :rank)"
    if level == "reported":
        # A shared credential is presented by its legitimate users all the time; that is only
        # evidence once the credential is already under review because a suspect agent saw it.
        condition += " AND attribute_exists(flag_rank)"
    now = utc_now_iso()
    try:
        aws.table(CREDENTIALS_TABLE).update_item(
            Key={"secret_ref": secret_ref},
            UpdateExpression=(
                "SET #s = :flagged, flag_level = :level, flag_rank = :rank, flag_updated_at = :now, "
                "flagged_at = if_not_exists(flagged_at, :now), flagged_by = if_not_exists(flagged_by, :by), "
                "#ev = if_not_exists(#ev, :ev)"),
            ConditionExpression=condition,
            ExpressionAttributeNames={"#s": "status", "#ev": f"flag_evidence_{level}"},
            ExpressionAttributeValues={
                ":flagged": "flagged", ":level": level, ":rank": RANK[level], ":now": now,
                ":by": evidence.get("agent_id", "system"), ":ev": {**evidence, "recorded_at": now},
                ":canary": "canary",
            },
        )
        return True
    except registry.conditional_failed():
        return False


def _fill_evidence(secret_ref: str, level: str, evidence: dict) -> None:
    """Already at this level or higher: keep the level, record this level's first evidence if missing."""
    try:
        aws.table(CREDENTIALS_TABLE).update_item(
            Key={"secret_ref": secret_ref},
            UpdateExpression="SET #ev = :ev",
            ConditionExpression=f"attribute_exists(flag_rank) AND flag_rank >= :rank AND "
                                f"{_not_canary()} AND attribute_not_exists(#ev)",
            ExpressionAttributeNames={"#ev": f"flag_evidence_{level}"},
            ExpressionAttributeValues={":ev": {**evidence, "recorded_at": utc_now_iso()},
                                       ":rank": RANK[level], ":canary": "canary"},
        )
    except registry.conditional_failed():
        pass  # first evidence already kept, or not flaggable


def raise_level(secret_ref: str, level: str, evidence: dict) -> dict | None:
    """Raise one credential to at least `level`.

    Returns {"secret_ref", "flag_level", "changed"} with the level after the call, or None
    when there is nothing to flag (not registered, a canary, or `reported` for a
    credential nobody flagged).
    """
    evidence = clean_evidence(evidence)
    _check(level, evidence)
    if not isinstance(secret_ref, str) or is_canary_ref(secret_ref):
        return None
    if _raise(secret_ref, level, evidence):
        return {"secret_ref": secret_ref, "flag_level": level, "changed": True}
    _fill_evidence(secret_ref, level, evidence)
    row = registry.get(secret_ref, consistent=True)
    if not row or row.get("secret_type") == "canary" or "flag_level" not in row:
        return None
    return {"secret_ref": secret_ref, "flag_level": row["flag_level"], "changed": False}


def raise_many(secret_refs, level: str, evidence: dict) -> list[dict]:
    if (not isinstance(secret_refs, list) or len(secret_refs) > MAX_REFS_PER_CALL
            or not all(isinstance(ref, str) for ref in secret_refs)):
        raise ValueError(f"secret_refs must be a list of at most {MAX_REFS_PER_CALL} strings")
    results = (raise_level(ref, level, evidence) for ref in dict.fromkeys(secret_refs))
    return [r for r in results if r is not None]
