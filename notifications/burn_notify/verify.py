"""Check a rendered provider payload: required facts present, nothing sensitive present.

Used by the tests and by the live check, so the evidence printed for a real send is the
same check the unit tests enforce.
"""
from __future__ import annotations

import json

from .incident import Incident

SENSITIVE_EVENT_FIELDS = ("detail", "destination", "fingerprint", "secret_ref", "tool", "actor", "encoding",
                          "owner_agent_id", "target_agent_id")


def _normalize(text: str) -> str:
    # Escapes differ per channel (Discord "\-", Slack "&amp;"); compare on the visible text.
    return text.replace("\\\\", "\\").replace("\\", "").replace("&amp;", "&")


def check_payload(incident: Incident, event: dict | None, body, *, secrets: tuple[str, ...] = ()) -> list[str]:
    """Return a list of problems (empty = pass). body: the provider request body (dict or str)."""
    raw = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    text = _normalize(raw)
    problems = []
    required = {"event id": incident.event_id, "affected agent": incident.agent_id,
                "severity": incident.severity.label, "timestamp": incident.ts}
    if incident.dashboard_url:
        required["dashboard link"] = incident.dashboard_url
    if incident.notice:
        required["test label"] = "[TEST]"
    for name, value in required.items():
        if value not in text:
            problems.append(f"missing {name}")
    allowed = {incident.agent_id, incident.session_id, incident.role}
    for field in SENSITIVE_EVENT_FIELDS:
        value = (event or {}).get(field)
        if value and len(value) > 6 and value not in allowed and value in text:
            problems.append(f"event field '{field}' leaked")
    for secret in secrets:
        if secret and (secret in raw or secret in text):
            problems.append("a destination secret appears in the payload")
    return problems
