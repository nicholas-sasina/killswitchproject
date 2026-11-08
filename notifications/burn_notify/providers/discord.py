"""Discord incoming webhook with one rich embed.

allowed_mentions.parse = [] makes Discord ignore every @everyone/@here/user/role
mention in the payload, and the text is escaped as well. Like Slack, a webhook is
bound to a channel inside Discord, so options.private_channel_confirmed = true is
required. `?wait=true` makes Discord return the created message, which is our ack.
"""
from __future__ import annotations

import json
from datetime import datetime

from .. import escape
from ..incident import Incident
from ..policy import Severity
from .base import Ack, DeliveryError, Destination, PreparedRequest, Provider, classify_http

COLORS = {Severity.CRITICAL: 0xB42318, Severity.HIGH: 0xC4320A, Severity.MEDIUM: 0xB08900,
          Severity.LOW: 0x5F6B7A, Severity.INFO: 0x8A94A6}
UPDATE_COLOR = 0x2B6CB0


def _iso(ts: str) -> str | None:
    try:
        return datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").isoformat()
    except ValueError:
        try:
            return datetime.strptime(ts.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").isoformat()
        except ValueError:
            return None


class DiscordProvider(Provider):
    name = "discord"
    exact_hosts = frozenset({"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"})
    path_prefixes = ("/api/webhooks/",)

    @classmethod
    def validate_destination(cls, dest: Destination) -> None:
        super().validate_destination(dest)
        if dest.options.get("private_channel_confirmed") is not True:
            raise ValueError(f"destination {dest.id}: set options.private_channel_confirmed=true after checking "
                             "the webhook posts to a private security channel")

    def build(self, incident: Incident, dest: Destination) -> PreparedRequest:
        esc = escape.discord
        embed: dict = {
            "title": escape.clean(f"{incident.header}: {incident.title}", 256),
            "description": esc(incident.summary, 2000),
            "color": UPDATE_COLOR if incident.is_update else COLORS[incident.severity],
            "fields": [{"name": esc(label, 250), "value": esc(value, 1000) or "-", "inline": len(value) < 40}
                       for label, value in incident.fields()][:25],
            "footer": {"text": "BURN ledger event; no credential values included"},
        }
        timestamp = _iso(incident.ts)
        if timestamp:
            embed["timestamp"] = timestamp
        if incident.dashboard_url:
            embed["url"] = incident.dashboard_url
        body = {"username": "BURN", "content": "", "embeds": [embed], "allowed_mentions": {"parse": []}}
        return PreparedRequest(url_ref=dest.secret_ref, body=body, preview={"provider": self.name, "body": body})

    def send(self, prepared: PreparedRequest, dest: Destination, secret: str | None) -> Ack:
        url = secret or ""
        url += ("&" if "?" in url else "?") + "wait=true"
        resp = self._post_json(url, prepared.body)
        if resp.status in (200, 204):
            try:
                message_id = json.loads(resp.body or b"{}").get("id")
            except (ValueError, AttributeError):
                message_id = None
            return Ack(provider_ref=message_id if isinstance(message_id, str) and message_id.isdigit() else None,
                       status=resp.status)
        if resp.status == 429:
            try:
                retry_after = json.loads(resp.body or b"{}").get("retry_after")
            except (ValueError, AttributeError):
                retry_after = None
            raise classify_http(resp, retry_after=retry_after if isinstance(retry_after, (int, float)) else None)
        raise classify_http(resp)
