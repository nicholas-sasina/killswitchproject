"""The safe, normalized incident every formatter renders.

An Incident is built from an allowlist of ledger fields plus policy text. It never
holds `detail`, `destination`, `fingerprint`, `secret_ref`, `tool`, `actor` or any
other raw event content, so a formatter cannot leak them even by mistake.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from .policy import CONTAINMENT_UPDATE, Classification, Severity

ALERTS_ROUTE = "/#/alerts"  # the only incident route the dashboard has (dashboard/src/router.ts)


def incident_id(event_id: str) -> str:
    """Opaque reference derived from the event id (never from a fingerprint)."""
    return "inc_" + hashlib.sha256(f"burn-notify:{event_id}".encode()).hexdigest()[:16]


def dashboard_link(origin: str | None) -> str | None:
    """https://host[:port] + the alerts route, or None. Anything else is refused."""
    if not origin:
        return None
    parts = urlsplit(origin.strip())
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("dashboard origin must be a bare https origin, e.g. https://burn.example.com")
    return f"https://{parts.netloc}{ALERTS_ROUTE}"


@dataclass(frozen=True)
class Incident:
    event_id: str
    incident_id: str
    ts: str
    severity: Severity
    category: str
    kind: str
    title: str
    summary: str
    agent_id: str
    session_id: str | None
    role: str | None
    flag_level: str | None
    facts: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    priority: bool = False
    dashboard_url: str | None = None
    aggregated_count: int = 1
    notice: str | None = None  # synthetic/test sends: shown first, header prefixed [TEST]

    @property
    def is_update(self) -> bool:
        return self.kind == CONTAINMENT_UPDATE

    @property
    def header(self) -> str:
        if self.is_update:
            text = "BURN CONTAINMENT UPDATE"
        elif self.aggregated_count > 1:
            text = f"BURN ALERT SUMMARY — {self.severity.label}"
        else:
            text = f"BURN SECURITY ALERT — {self.severity.label}"
        return f"[TEST] {text}" if self.notice else text

    def fields(self) -> list[tuple[str, str]]:
        """Ordered (label, value) pairs every channel shows."""
        rows: list[tuple[str, str]] = [("Notice", self.notice)] if self.notice else []
        rows += [("Severity", self.severity.label), ("Affected instance", self.agent_id)]
        if self.session_id:
            rows.append(("Session", self.session_id))
        if self.role:
            rows.append(("Role", self.role))
        labels = {label for label, _ in self.facts}
        if self.flag_level and "Flag level" not in labels and "Flag level at dismissal" not in labels:
            rows.append(("Flag level", self.flag_level))
        rows.extend(self.facts)
        if self.aggregated_count > 1:
            rows.append(("Related events", str(self.aggregated_count)))
        rows.append(("Time", self.ts))
        rows.append(("Event ID", self.event_id))
        rows.append(("Incident", self.incident_id))
        return rows

    def subject(self) -> str:
        tag = "UPDATE" if self.is_update else self.severity.label
        test = "[TEST]" if self.notice else ""
        return f"{test}[BURN][{tag}] {self.title} — {self.agent_id}"[:200]


def build(event: dict, result: Classification, *, dashboard_url: str | None,
          extra_facts: list[tuple[str, str]] | None = None, notice: str | None = None) -> Incident:
    facts = list(result.facts)
    for label, value in extra_facts or []:
        facts = [(lab, val) for lab, val in facts if lab != label] + [(label, value)]
    return Incident(
        event_id=event["event_id"], incident_id=incident_id(event["event_id"]), ts=event["ts"],
        severity=result.severity, category=result.category, kind=result.kind, title=result.title,
        summary=result.summary, agent_id=event["agent_id"], session_id=event.get("session_id"),
        role=event.get("role"), flag_level=event.get("flag_level"), facts=tuple(facts),
        priority=result.priority, dashboard_url=dashboard_url, notice=notice,
    )


def aggregate(sample: Incident, count: int, window_label: str) -> Incident:
    """One summary standing for `count` similar incidents (same category and agent)."""
    summary = (f"BURN recorded {count} related '{sample.category}' events for {sample.agent_id} "
               f"in {window_label}. {sample.summary}")
    if sample.category == "digest":
        summary = (f"{count} low-severity BURN events were held for this digest ({window_label}). "
                   "Open the dashboard for the full list.")
    elif sample.category == "disclosure_blocked":
        summary = (f"BURN blocked {count} related requests from {sample.agent_id} in {window_label}. "
                   "No successful disclosure has been established by these events.")
    return replace(sample, summary=summary, aggregated_count=count,
                   incident_id=incident_id(f"agg:{sample.category}:{sample.agent_id}:{window_label}"))
