"""Flag levels: how worried to be about a credential an agent saw but does not own.

    seen       it entered an agent's context; no sign it was sent anywhere
    attempted  an exit check or tool-argument check blocked it from leaving
    reported   trusted evidence it was presented at a monitored service

Levels only go up. Flagging never rotates anything: shared credentials are
rotated by their human owner after review. State lives on the credential's
burn-credentials row:

    status = "flagged"                       (unchanged meaning for older readers)
    flag_level, flag_rank (1..3)             highest level reached; the rank makes "only go up"
                                             a single conditional update
    flag_evidence_<level>                    the FIRST evidence that reached each level
    flag_instances                           distinct agent ids (= instances) behind the flag,
                                             capped at MAX_INSTANCES ids
    flag_state, flag_resolution              open / dismissed, and the owner's last decision
    flagged_at, flagged_by, flag_updated_at

One flag per credential. At most three evidence attributes, so duplicate or
repeated events never grow the row; re-sending the same event changes nothing.
Owners resolve a flag with a reason (resolve()): rotate mints a new version and
clears the flag, dismiss closes it. A dismissed flag reopens only when evidence
arrives at a higher level than the level it was dismissed at.
"""
import re

from boto3.dynamodb.conditions import Attr
from burn_contracts import fingerprint as fp
from burn_contracts.events import FLAG_LEVELS, utc_now_iso
from burn_contracts.names import CREDENTIALS_TABLE, SESSION_ID_RE, is_canary_ref

import aws
import fingerprints
import registry

RANK = {level: rank for rank, level in enumerate(FLAG_LEVELS, start=1)}

# Which event sources may vouch for which level.
SOURCES_FOR = {
    "seen": {"tool_response", "handoff", "burn"},
    "attempted": {"egress", "gateway", "handoff"},
    "reported": {"provider"},
}
MAX_REFS_PER_CALL = 100
MAX_INSTANCES = 500  # ids kept per flag; the count stops growing there
REASON_LENGTH = (3, 300)
BY_RE = re.compile(r"^[A-Za-z0-9 ._@-]{1,64}$")


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass

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
                "SET #s = :flagged, flag_level = :level, flag_rank = :rank, flag_updated_at = :now, flag_state = :open, "
                "flagged_at = if_not_exists(flagged_at, :now), flagged_by = if_not_exists(flagged_by, :by), "
                "#ev = if_not_exists(#ev, :ev)"),
            ConditionExpression=condition,
            ExpressionAttributeNames={"#s": "status", "#ev": f"flag_evidence_{level}"},
            ExpressionAttributeValues={
                ":flagged": "flagged", ":level": level, ":rank": RANK[level], ":now": now, ":open": "open",
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


def _add_instance(secret_ref: str, agent_id: str | None) -> None:
    """Count the instance behind this evidence (a string set dedupes; capped)."""
    if not agent_id or agent_id == "system":
        return
    try:
        aws.table(CREDENTIALS_TABLE).update_item(
            Key={"secret_ref": secret_ref}, UpdateExpression="ADD flag_instances :a",
            ConditionExpression="attribute_exists(flag_rank) AND (attribute_not_exists(flag_instances) "
                                "OR size(flag_instances) < :max OR contains(flag_instances, :id))",
            ExpressionAttributeValues={":a": {agent_id}, ":id": agent_id, ":max": MAX_INSTANCES},
        )
    except registry.conditional_failed():
        pass  # not flagged, or the cap is reached


def raise_level(secret_ref: str, level: str, evidence: dict) -> dict | None:
    """Raise one credential to at least `level` and count the instance behind the evidence.

    Returns {"secret_ref", "flag_level", "changed", "instance_count"} after the call, or None
    when there is nothing to flag (not registered, a canary, or `reported` for a
    credential nobody flagged).
    """
    evidence = clean_evidence(evidence)
    _check(level, evidence)
    if not isinstance(secret_ref, str) or is_canary_ref(secret_ref):
        return None
    changed = _raise(secret_ref, level, evidence)
    if not changed:
        _fill_evidence(secret_ref, level, evidence)
    _add_instance(secret_ref, evidence.get("agent_id"))
    row = registry.get(secret_ref, consistent=True)
    if not row or row.get("secret_type") == "canary" or "flag_level" not in row:
        return None
    return {"secret_ref": secret_ref, "flag_level": row["flag_level"], "changed": changed,
            "instance_count": len(row.get("flag_instances") or ())}


def view(row: dict, state: str | None = None) -> dict:
    """The contract's Flag shape (contracts/minter.openapi.yaml). No values, ids only."""
    return {
        "secret_ref": row["secret_ref"], "secret_type": row.get("secret_type"),
        "owner_agent_id": row.get("owner_agent_id"), "flag_level": row["flag_level"],
        "instance_count": len(row.get("flag_instances") or ()), "state": state or row.get("flag_state", "open"),
        "flagged_at": row.get("flagged_at"), "updated_at": row.get("flag_updated_at"),
        "evidence": {lvl: row[f"flag_evidence_{lvl}"] for lvl in FLAG_LEVELS if f"flag_evidence_{lvl}" in row},
        "resolution": row.get("flag_resolution"),
    }


def list_flags() -> list[dict]:
    kwargs = {"FilterExpression": Attr("flag_rank").exists()}
    rows = []
    while True:
        page = aws.table(CREDENTIALS_TABLE).scan(**kwargs)
        rows += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    flags = [view(r) for r in rows if r.get("secret_type") != "canary"]
    flags.sort(key=lambda f: f["updated_at"] or "", reverse=True)
    return sorted(flags, key=lambda f: (f["state"] != "open", -RANK[f["flag_level"]]))


def _clean_reason(reason) -> str:
    if not isinstance(reason, str):
        raise ValueError("reason is required")
    reason = " ".join(reason.split())
    if not REASON_LENGTH[0] <= len(reason) <= REASON_LENGTH[1] or not reason.isprintable():
        raise ValueError(f"reason must be {REASON_LENGTH[0]}-{REASON_LENGTH[1]} printable characters")
    hmac_key = fingerprints.key()
    for token in {reason, *re.split(r"[\s\"'`,;()<>]+", reason)}:
        if len(token) >= 8 and fingerprints.get(fp.fingerprint(token, hmac_key)):
            raise ValueError("reason contains a registered credential; describe it instead")
    return reason


def _flagged_row(secret_ref) -> dict:
    row = registry.get(secret_ref, consistent=True) if isinstance(secret_ref, str) else None
    if not row or row.get("secret_type") == "canary" or "flag_rank" not in row:
        raise NotFound("not a flagged credential")
    return row


def resolve(secret_ref: str, action: str, reason: str, by: str | None, rotate_credential) -> dict:
    """Owner decision. rotate_credential(row) -> new version, for action "rotate"."""
    if action not in ("rotate", "dismiss"):
        raise ValueError("action must be rotate or dismiss")
    if by is not None and (not isinstance(by, str) or not BY_RE.match(by)):
        raise ValueError("invalid by")
    reason = _clean_reason(reason)
    row = _flagged_row(secret_ref)
    decision = {"action": action, "reason": reason, "by": by, "at": utc_now_iso(), "level": row["flag_level"]}
    if action == "dismiss":
        try:  # only the level the owner looked at: a raise in between needs a fresh review
            new = aws.table(CREDENTIALS_TABLE).update_item(
                Key={"secret_ref": secret_ref},
                UpdateExpression="SET flag_state = :d, flag_resolution = :r, #s = :active, flag_updated_at = :t",
                ConditionExpression="flag_rank = :rank",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":d": "dismissed", ":r": decision, ":active": "active",
                                           ":t": decision["at"], ":rank": row["flag_rank"]},
                ReturnValues="ALL_NEW")["Attributes"]
        except registry.conditional_failed():
            raise Conflict("the flag changed while it was being reviewed; review it again")
        return {"flag": view(new), "rotated_to_version": None}
    version = rotate_credential(row)
    final = view({**row, "flag_resolution": decision}, state="rotated")
    # The new version is a different secret: it starts unflagged. Keep a summary of the old flag.
    aws.table(CREDENTIALS_TABLE).update_item(
        Key={"secret_ref": secret_ref},
        UpdateExpression="SET #s = :active, last_flag = :summary REMOVE flag_level, flag_rank, flag_state, "
                         "flag_resolution, flag_instances, flagged_at, flagged_by, flag_updated_at, "
                         "flag_evidence_seen, flag_evidence_attempted, flag_evidence_reported",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":active": "active", ":summary": {
            "flag_level": final["flag_level"], "instance_count": final["instance_count"], "resolution": decision}},
    )
    return {"flag": final, "rotated_to_version": version}


def raise_many(secret_refs, level: str, evidence: dict) -> list[dict]:
    if (not isinstance(secret_refs, list) or len(secret_refs) > MAX_REFS_PER_CALL
            or not all(isinstance(ref, str) for ref in secret_refs)):
        raise ValueError(f"secret_refs must be a list of at most {MAX_REFS_PER_CALL} strings")
    results = (raise_level(ref, level, evidence) for ref in dict.fromkeys(secret_refs))
    return [r for r in results if r is not None]
