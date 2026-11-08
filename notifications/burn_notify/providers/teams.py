"""Microsoft Teams via a Workflows (Power Automate) webhook trigger.

Uses the "When a Teams webhook request is received" workflow, which accepts a
message with an Adaptive Card attachment and posts it to the channel chosen in the
workflow. Retired Office 365 connector endpoints (*.webhook.office.com,
outlook.office.com) are refused. Setup is in notifications/README.md.

The workflow trigger answers 202 Accepted when it has queued the run. That is an
acknowledgement of receipt by the workflow, not proof the card was posted.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .. import escape
from ..incident import Incident
from ..policy import Severity
from .base import Ack, DeliveryError, Destination, PreparedRequest, Provider, classify_http

_COLOR = {Severity.CRITICAL: "Attention", Severity.HIGH: "Attention", Severity.MEDIUM: "Warning",
          Severity.LOW: "Default", Severity.INFO: "Default"}
RETIRED_SUFFIXES = (".webhook.office.com", "outlook.office.com", "outlook.office365.com")


class TeamsProvider(Provider):
    name = "teams"
    host_suffixes = (".logic.azure.com", ".environment.api.powerplatform.com")
    path_prefixes = ("/workflows/", "/powerautomate/automations/direct/workflows/")

    def build(self, incident: Incident, dest: Destination) -> PreparedRequest:
        esc = escape.teams
        card: dict = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "msteams": {"width": "Full"},
            "body": [
                {"type": "TextBlock", "text": esc(incident.header), "weight": "Bolder", "size": "Medium",
                 "color": "Accent" if incident.is_update else _COLOR[incident.severity],
                 "wrap": True},
                {"type": "TextBlock", "text": esc(incident.title), "weight": "Bolder", "wrap": True},
                {"type": "TextBlock", "text": esc(incident.summary, 1500), "wrap": True},
                {"type": "FactSet", "facts": [{"title": esc(label), "value": esc(value)}
                                              for label, value in incident.fields()]},
            ],
        }
        if incident.dashboard_url:
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Investigate in Burn",
                                "url": incident.dashboard_url}]
        body = {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}
        return PreparedRequest(url_ref=dest.secret_ref, body=body, preview={"provider": self.name, "body": body})

    def send(self, prepared: PreparedRequest, dest: Destination, secret: str | None) -> Ack:
        host = (urlsplit(secret or "").hostname or "").lower()
        if host.endswith(RETIRED_SUFFIXES):
            raise DeliveryError("retired_connector_endpoint", retryable=False)
        resp = self._post_json(secret or "", prepared.body)
        if resp.status in (200, 202):
            return Ack(status=resp.status)
        raise classify_http(resp)
