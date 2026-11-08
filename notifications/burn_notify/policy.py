"""Event -> alert classification policy.

Every rule reads only the contract's structured fields (source, action, flag_level,
secret_ref shape, owner). In a few places the Burn API and the mock provider record
the outcome only in `detail`, written by Burn itself from fixed templates
(burn_api/lambdas/api/canary.py, minter/lambdas/provider/canary_trip.py). For those
two trusted writers (source "burn" and "provider") we match the fixed template
*prefixes* to pick a category. The detail text itself is never copied into a message.

Severity is a proposal, not evidence. The wording rules this module enforces:
  * canary_used alone does not mean containment succeeded;
  * a blocked request does not mean data left;
  * a flag at `seen` does not mean compromise;
  * a rotated event covers one credential, never "everything was rotated";
  * a failed or partial containment is never called successful.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

from .events import FLAG_LEVELS, is_canary_ref


class Severity(enum.IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, name: str) -> "Severity":
        try:
            return cls[str(name).upper()]
        except KeyError as exc:
            raise ValueError(f"unknown severity {name!r}") from exc

    @property
    def label(self) -> str:
        return self.name


ALERT = "alert"
CONTAINMENT_UPDATE = "containment_update"

# Category -> default severity. Destinations filter on these category names.
CATEGORIES: dict[str, Severity] = {
    "containment_failed": Severity.CRITICAL,
    "containment_partial": Severity.HIGH,
    "canary_not_contained": Severity.HIGH,
    "canary_report_only": Severity.HIGH,
    "canary_used": Severity.HIGH,
    "canary_used_in_burn": Severity.MEDIUM,
    "canary_contained": Severity.HIGH,       # containment update after a canary incident
    "flag_reported": Severity.HIGH,
    "disclosure_blocked": Severity.HIGH,
    "flag_attempted": Severity.MEDIUM,
    "replacement_failed": Severity.MEDIUM,   # burned instance stays burned; its task needs a person
    "flag_review": Severity.MEDIUM,
    "unregistered_access": Severity.MEDIUM,
    "agent_burned": Severity.MEDIUM,          # containment update after a targeted burn
    "stale_key_rejected": Severity.MEDIUM,
    "egress_blocked": Severity.MEDIUM,
    "flag_seen": Severity.LOW,
    "credential_rotated": Severity.LOW,
    "flag_dismissed": Severity.LOW,
    "access_refused": Severity.LOW,
    "instance_replaced": Severity.LOW,
    "task_resumed": Severity.LOW,
    "activity": Severity.INFO,
}

# Categories a config override may never push below HIGH: they always need a person.
CRITICAL_FLOOR = frozenset({"containment_failed", "containment_partial", "canary_not_contained"})

# Categories that are never folded into an aggregate, whatever their severity.
NEVER_AGGREGATE = frozenset({
    "containment_failed", "containment_partial", "canary_not_contained", "canary_report_only",
    "canary_used", "canary_contained", "agent_burned", "flag_reported",
})

_CHANNEL = {"egress": "egress proxy", "gateway": "MCP Gateway", "handoff": "agent-to-agent handoff",
            "tool_response": "tool response check"}

# Fixed template prefixes from Burn's own writers (see module docstring).
_ROTATION_FAILED = "rotation failed:"
_ROTATION_DEFERRED = "rotation deferred:"
_CANARY_NOT_CONTAINED = "canary incident not contained:"
_REPORT_ONLY_MARK = "is report-only"
_REPLACEMENT_NOT_RECORDED = "replacement not recorded"
_SESSIONS_REVOKED_RE = re.compile(r"^(\d{1,6}) session\(s\) revoked")


@dataclass(frozen=True)
class Classification:
    category: str
    severity: Severity
    kind: str                      # ALERT or CONTAINMENT_UPDATE
    title: str                     # fixed text chosen by policy
    summary: str                   # fixed text chosen by policy
    facts: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # fixed labels, safe values
    priority: bool = False         # high-priority style (canary misuse, failed containment)
    aggregatable: bool = True

    @property
    def aggregate_key_part(self) -> str:
        return self.category


def _detail(event: dict) -> str:
    """Detail text, but only from Burn's own writers; used for prefix matching only."""
    if event["source"] in ("burn", "provider"):
        return (event.get("detail") or "").strip()
    return ""


def _canary_origin(event: dict) -> str:
    detail = _detail(event)
    if "used through its own session" in detail:
        return "own_session"
    if "used through another Burn session" in detail:
        return "burn_session"
    return "outside"  # the provider's default template, and the safe (louder) assumption


def _c(category: str, kind: str, title: str, summary: str, facts=(), priority=False) -> Classification:
    return Classification(category=category, severity=CATEGORIES[category], kind=kind, title=title,
                          summary=summary, facts=tuple(facts), priority=priority,
                          aggregatable=category not in NEVER_AGGREGATE)


def classify(event: dict) -> Classification:
    action, source = event["action"], event["source"]
    detail = _detail(event)
    level = event.get("flag_level")

    if action == "canary_used":
        origin = _canary_origin(event)
        if origin == "outside":
            return _c("canary_used", ALERT, "Canary credential presented at monitored provider",
                      "A decoy credential planted in one Burn session was presented to the monitored provider "
                      "from outside Burn. The provider rejected it.",
                      [("Signal", "Canary use detected"), ("Containment", "Awaiting verified outcome"),
                       ("Credential action", "Not yet confirmed")], priority=True)
        where = "its own Burn session" if origin == "own_session" else "another Burn session"
        return _c("canary_used_in_burn", ALERT, "Canary credential used inside Burn",
                  f"A session's decoy credential was used through {where}. Burn records this as an alert; "
                  "it does not burn the session for in-Burn use.",
                  [("Signal", "Canary use detected"), ("Containment", "Not requested (alert only)")])

    if action == "flagged":
        if detail.startswith(_ROTATION_FAILED):
            return _c("containment_failed", ALERT, "Containment incomplete: credential rotation failed",
                      "The session was revoked, but rotating its credentials failed. The credentials were NOT "
                      "rotated. A person must burn the agent manually.",
                      [("Status", "Session revoked"), ("Owned credentials", "NOT rotated (rotation failed)"),
                       ("Containment", "Incomplete - action required")], priority=True)
        if detail.startswith(_ROTATION_DEFERRED):
            return _c("containment_partial", ALERT, "Containment partial: credential rotation deferred",
                      "The session was revoked, but its credentials are still used by other active sessions, so "
                      "they were not rotated. Review and rotate manually.",
                      [("Status", "Session revoked"), ("Owned credentials", "Not rotated (deferred)"),
                       ("Containment", "Partial - review required")], priority=True)
        if detail.startswith(_CANARY_NOT_CONTAINED):
            if _REPORT_ONLY_MARK in detail:
                return _c("canary_report_only", ALERT, "Canary used: report-only role, nothing burned",
                          "A canary was used from outside Burn, but the instance's role is report-only, so Burn "
                          "did not revoke anything. Review the instance.",
                          [("Containment", "Not performed (report-only role)")], priority=True)
            return _c("canary_not_contained", ALERT, "Canary incident not contained automatically",
                      "Burn could not verify who the canary belonged to, so it did not contain anything "
                      "automatically. A person must review the incident.",
                      [("Containment", "Not performed - human review required")], priority=True)
        if detail.startswith(_REPLACEMENT_NOT_RECORDED):
            return _c("replacement_failed", ALERT, "Replacement not recorded for a burned instance",
                      "The instance stays burned, but Burn could not record its replacement, so its launcher will "
                      "not replace it. Restart its task by hand.",
                      [("Status", "Burned; replacement not recorded"), ("Action", "Restart the task manually")])
        if level == "reported":
            if source == "provider":
                return _c("flag_reported", ALERT, "Flagged credential presented at monitored provider",
                          "A credential already under review was presented at a monitored provider, so its flag "
                          "was raised to `reported`. Its owner should rotate it.", [("Flag level", "reported")])
            return _c("flag_reported", ALERT, "Credential flag escalated to reported",
                      "A credential this agent does not own reached review level `reported`. Its owner should "
                      "rotate or dismiss it.", [("Flag level", "reported")])
        if level == "attempted":
            return _c("flag_attempted", ALERT, "Credential flag raised to attempted",
                      "The agent tried to send out a credential it does not own; a check blocked it. "
                      "This is not evidence the credential left.", [("Flag level", "attempted")])
        if level == "seen":
            return _c("flag_seen", ALERT, "Credential flagged as seen",
                      "The agent saw a credential it does not own. Seen is not evidence of compromise.",
                      [("Flag level", "seen")])
        return _c("flag_review", ALERT, "Credential flagged for review",
                  "Burn flagged a credential for owner review.", [])

    if action == "revoked":
        if source == "burn" and is_canary_ref(event.get("secret_ref")):
            return _c("canary_contained", CONTAINMENT_UPDATE, "Canary session revoked",
                      "Burn revoked the one session whose canary was used.",
                      [("Status", "Session revoked"), ("Other instances", "No broad containment requested")])
        match = _SESSIONS_REVOKED_RE.match(detail)
        count = f"{int(match.group(1))} session(s) revoked" if match else "Badge revoked"
        return _c("agent_burned", CONTAINMENT_UPDATE, "Agent burned",
                  "Burn revoked this agent's badge.",
                  [("Status", count), ("Scope of this event", "This agent only")])

    if action == "blocked":
        channel = _CHANNEL.get(source, "Burn check")
        if event.get("secret_ref") or event.get("fingerprint") or event.get("secret_type"):
            return _c("disclosure_blocked", ALERT, "Credential disclosure blocked",
                      f"The {channel} stopped a request carrying a known credential. A blocked attempt is not "
                      "evidence that data left.", [("Blocked by", channel), ("Outcome", "Blocked")])
        return _c("egress_blocked", ALERT, "Outbound request blocked by policy",
                  f"The {channel} blocked an outbound request.", [("Blocked by", channel), ("Outcome", "Blocked")])

    if action == "unregistered":
        return _c("unregistered_access", ALERT, "Unregistered badge denied",
                  "A call with a badge Burn never issued was refused.", [("Outcome", "Refused")])
    if action == "refused":
        return _c("access_refused", ALERT, "Call refused",
                  "Burn refused a call from a revoked or unknown badge, or for a disallowed tool.",
                  [("Outcome", "Refused")])
    if action == "rejected":
        return _c("stale_key_rejected", ALERT, "Rotated credential presented and rejected",
                  "The provider refused a credential version that Burn had already rotated.",
                  [("Outcome", "Rejected by provider")])
    if action == "rotated":
        return _c("credential_rotated", ALERT, "Credential rotated",
                  "Burn rotated one credential; this event covers that credential only.",
                  [("Credential action", "One credential rotated")])
    if action == "dismissed":
        facts = [("Flag level at dismissal", level)] if level in FLAG_LEVELS else []
        return _c("flag_dismissed", ALERT, "Credential flag dismissed",
                  "An owner reviewed a flagged credential and dismissed the flag; it was not rotated.", facts)
    if action == "replaced":
        return _c("instance_replaced", ALERT, "Burned instance replaced",
                  "A burned instance was linked to a fresh replacement instance.", [])
    if action == "resumed":
        return _c("task_resumed", ALERT, "Replacement resumed task",
                  "A replacement instance took over a burned instance's task.", [])
    return _c("activity", ALERT, "Burn activity", "Routine Burn activity.", [])


def apply_overrides(result: Classification, overrides: dict[str, Severity]) -> Classification:
    """Config may re-rank categories, but never below HIGH for the critical-floor set."""
    wanted = overrides.get(result.category)
    if wanted is None:
        return result
    if result.category in CRITICAL_FLOOR:
        wanted = max(wanted, Severity.HIGH)
    if wanted == result.severity:
        return result
    return Classification(**{**result.__dict__, "severity": wanted,
                             "aggregatable": result.aggregatable and wanted < Severity.CRITICAL})
