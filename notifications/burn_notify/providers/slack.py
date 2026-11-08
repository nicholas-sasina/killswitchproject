"""Slack incoming webhook (Block Kit).

An incoming webhook is bound to one channel when the Slack app is installed, so the
channel is chosen in Slack, not here. Because Burn cannot check that channel's
visibility, a Slack destination must set options.private_channel_confirmed = true;
without it the destination is rejected at config load (no public posting by default).

Slack answers 200 with body "ok" on success; anything else is a failure.
"""
from __future__ import annotations

from .. import escape
from ..incident import Incident
from .base import Ack, DeliveryError, Destination, PreparedRequest, Provider, classify_http

_PERMANENT_BODIES = {"invalid_token", "action_prohibited", "no_service", "no_service_id", "no_team",
                     "team_disabled", "channel_not_found", "channel_is_archived", "invalid_payload",
                     "user_not_found", "posting_to_general_channel_denied"}


class SlackProvider(Provider):
    name = "slack"
    exact_hosts = frozenset({"hooks.slack.com"})
    path_prefixes = ("/services/",)  # incoming webhooks; Workflow Builder /triggers/ take a different payload

    @classmethod
    def validate_destination(cls, dest: Destination) -> None:
        super().validate_destination(dest)
        if dest.options.get("private_channel_confirmed") is not True:
            raise ValueError(f"destination {dest.id}: set options.private_channel_confirmed=true after checking "
                             "the webhook posts to a private security channel")

    def build(self, incident: Incident, dest: Destination) -> PreparedRequest:
        esc = escape.slack
        icon = ":rotating_light:" if incident.priority else (":shield:" if incident.is_update else ":warning:")
        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": escape.clean(incident.header, 150),
                                        "emoji": False}},
            {"type": "section", "text": {"type": "mrkdwn",
                                         "text": f"{icon} *{esc(incident.title)}*\n{esc(incident.summary, 1500)}"}},
        ]
        fields = [{"type": "mrkdwn", "text": f"*{esc(label)}*\n{esc(value)}"} for label, value in incident.fields()]
        for i in range(0, len(fields), 10):  # Slack allows at most 10 fields per section
            blocks.append({"type": "section", "fields": fields[i:i + 10]})
        if incident.dashboard_url:
            blocks.append({"type": "actions", "elements": [{
                "type": "button", "text": {"type": "plain_text", "text": "Investigate in Burn"},
                "url": incident.dashboard_url, "action_id": "burn_open_alerts"}]})
        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn", "text": esc("Generated from Burn ledger events; no credential values are included.")}]})
        body = {"text": esc(f"{incident.header}: {incident.title} ({incident.agent_id})"), "blocks": blocks,
                "unfurl_links": False, "unfurl_media": False}
        return PreparedRequest(url_ref=dest.secret_ref, body=body, preview={"provider": self.name, "body": body})

    def send(self, prepared: PreparedRequest, dest: Destination, secret: str | None) -> Ack:
        resp = self._post_json(secret or "", prepared.body)
        text = resp.body.decode("utf-8", "replace").strip()
        if resp.status == 200 and text == "ok":
            return Ack(status=200)
        if resp.status == 429:
            raise classify_http(resp)
        if text in _PERMANENT_BODIES:
            raise DeliveryError(f"slack_{text}", retryable=False, status=resp.status)
        if resp.status == 200:  # 200 without "ok" is not an acknowledgement
            raise DeliveryError("unacknowledged", retryable=True, status=200)
        raise classify_http(resp)
