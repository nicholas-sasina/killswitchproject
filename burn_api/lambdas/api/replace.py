"""Replace on burn, at burn time: record what a clean replacement of a burned instance may resume.

When one instance of a role with `replace_on_burn` is burned (scope instance, a burn by source, or a canary
burn; never a role, lineage or token burn, which mean stop that whole group), Burn writes `replacement` on the
burned session: {status, cutoff, cutoff_source, done, quarantined}. Its launcher reads it, joins a fresh instance
and links it with POST /instances/{old}/replace. Burn never starts anything itself.

- cutoff: the earliest evidence of compromise on Burn's clock (cutoff_source "evidence"): the evidence the burn
  path passed (a canary incident's arrival, or for a burn by source this instance's first read of the source),
  or a blocked call, refused call or canary use recorded against this session. With none before the burn, the
  burn time (cutoff_source "burn_time").
- done / quarantined: burn_contracts.progress.classify. Only items done before the cutoff are trusted.
- status: pending, or needs_person once the instance's generation reaches names.MAX_REPLACEMENT_GENERATION.

Only the burn that revoked a session marks it, and the write is conditional (no replacement yet), so a later
burn of the same instance never adds or rewrites one. This runs after revoke and rotate, and is best effort:
an error becomes a flagged event and the burn's own result never changes.
"""
from datetime import datetime

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from burn_contracts import names, progress
from burn_contracts.events import utc_now_iso

import db
import errors

# Events on Burn's clock that show an instance was compromised by then.
EVIDENCE_ACTIONS = ("blocked", "refused", "canary_used")


def after_burn(agent_id: str, replaceable: list[str] | None, revoked: list[str],
               evidence_time: str | None = None) -> None:
    """Mark each session this burn revoked for replacement, if its role asks for one. Never raises.

    replaceable: None for a burn that never replaces (an agent, role, lineage or token burn); otherwise the
    sessions the caller already revoked for this burn. revoked: the sessions burn_agent revoked itself.
    """
    if replaceable is None:
        return
    for session_id in dict.fromkeys([*replaceable, *revoked]):
        try:
            mark(session_id, evidence_time)
        except Exception as exc:
            _report_failure(agent_id, session_id, exc)


def mark(session_id: str, evidence_time: str | None = None) -> dict | None:
    """Write the replacement record on one burned session; None when it gets none (or already has one)."""
    sessions = db.table(names.SESSIONS_TABLE)
    session = sessions.get_item(Key={"session_id": session_id}, ConsistentRead=True).get("Item")
    if (not session or session.get("status") != "revoked" or session.get("replacement")
            or not _replaces_on_burn(session.get("role"))):
        return None
    evidence = ([evidence_time] if evidence_time else []) + _evidence(session["agent_id"], session_id)
    cutoff, cutoff_source = _cutoff(evidence, session.get("revoked_at") or utc_now_iso())
    done, quarantined = progress.classify(session.get("progress"), cutoff)
    generation = int(session.get("generation") or 0)
    record = {"status": "pending" if generation < names.MAX_REPLACEMENT_GENERATION else "needs_person",
              "cutoff": cutoff, "cutoff_source": cutoff_source, "done": done, "quarantined": quarantined}
    try:
        sessions.update_item(
            Key={"session_id": session_id}, UpdateExpression="SET replacement = :r",
            # A row may carry replacement: null until it is burned.
            ConditionExpression="(attribute_not_exists(replacement) OR attribute_type(replacement, :null))"
                                " AND #s = :revoked",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":r": record, ":null": "NULL", ":revoked": "revoked"})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None  # another burn recorded it first
        raise
    return record


def _replaces_on_burn(role: str | None) -> bool:
    """The role's replace_on_burn; false when absent, and for sessions with no stored role (scenario agents)."""
    if not role:
        return False
    item = db.table(names.ROLES_TABLE).get_item(Key={"name": role}).get("Item") or {}
    return item.get("replace_on_burn") is True


def _evidence(agent_id: str, session_id: str) -> list[str]:
    """Times of blocked calls, refused calls and canary uses recorded for this session (each instance has its
    own agent id; a scenario agent's other sessions are left out)."""
    rows = db.query_all(db.table(names.EVENTS_TABLE), IndexName="by_agent",
                        KeyConditionExpression=Key("agent_id").eq(agent_id),
                        FilterExpression=Attr("action").is_in(list(EVIDENCE_ACTIONS)))
    return [r["ts"] for r in rows if r.get("ts") and r.get("session_id") in (None, session_id)]


def _iso(moment: datetime) -> str:
    """Burn's timestamp format (UTC, milliseconds, Z). Truncating never moves a cutoff later."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _cutoff(evidence: list[str], burn_time: str) -> tuple[str, str]:
    """(cutoff, cutoff_source): the earliest evidence, unless there is none at or before the burn."""
    burned = progress.parse_time(burn_time)
    moments = []
    for value in evidence:
        try:
            moments.append(progress.parse_time(value))
        except ValueError:
            continue  # an unreadable time is no evidence
    earliest = min(moments, default=None)
    if earliest is not None and earliest <= burned:
        return _iso(earliest), "evidence"
    return _iso(burned), "burn_time"


def _report_failure(agent_id: str, session_id: str, exc: Exception) -> None:
    """Say so on the ledger, without exception text (it may carry request details)."""
    what = "rate limited" if errors.is_throttle(exc) else type(exc).__name__
    print(f"replacement not recorded for {session_id}: {what}")
    try:
        db.write_event(agent_id=agent_id, source="burn", action="flagged", session_id=session_id,
                       detail=f"replacement not recorded ({what}): the instance stays burned and its launcher "
                              "will not replace it; restart its task by hand")
    except Exception as again:
        print(f"could not record that either for {session_id}: {type(again).__name__}")
