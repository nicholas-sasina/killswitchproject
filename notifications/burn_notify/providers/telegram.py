"""Telegram Bot API sendMessage.

The bot token is the secret; the chat id is configuration and must also appear in
options.allowed_chat_ids (an explicit allowlist of private chats / authorized groups).
The bot only sends: it exposes no commands, so nothing received over Telegram can
trigger a burn.
"""
from __future__ import annotations

import json
import re

from .. import escape
from ..incident import Incident
from .base import Ack, DeliveryError, Destination, PreparedRequest, Provider, classify_http

TOKEN_RE = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{30,64}$")
CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")
MAX_TEXT = 4096


class TelegramProvider(Provider):
    name = "telegram"
    exact_hosts = frozenset({"api.telegram.org"})
    path_prefixes = ("/bot",)

    @classmethod
    def validate_destination(cls, dest: Destination) -> None:
        super().validate_destination(dest)
        chat = str(dest.recipient or "")
        if not CHAT_ID_RE.match(chat):
            raise ValueError(f"destination {dest.id}: recipient must be a numeric Telegram chat id")
        allowed = {str(c) for c in dest.options.get("allowed_chat_ids", [])}
        if chat not in allowed:
            raise ValueError(f"destination {dest.id}: chat id is not in options.allowed_chat_ids")

    def build(self, incident: Incident, dest: Destination) -> PreparedRequest:
        esc = escape.telegram_html
        icon = "\U0001F6A8" if incident.priority else ("\U0001F6E1" if incident.is_update else "⚠️")
        lines = [f"{icon} <b>{esc(incident.header)}</b>", "", f"<b>{esc(incident.title)}</b>",
                 esc(incident.summary, 1500), ""]
        lines += [f"<b>{esc(label)}:</b> <code>{esc(value)}</code>" for label, value in incident.fields()]
        text = "\n".join(lines)
        if len(text) > MAX_TEXT:
            text = text[: MAX_TEXT - 1] + "…"
        body: dict = {
            "chat_id": str(dest.recipient),
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "disable_notification": incident.severity.value < 2,  # low/info arrive silently
            "protect_content": True,
        }
        if incident.dashboard_url:
            body["reply_markup"] = {"inline_keyboard": [[{"text": "Investigate in BURN",
                                                          "url": incident.dashboard_url}]]}
        return PreparedRequest(url_ref=dest.secret_ref, body=body, preview={"provider": self.name, "body": body})

    def send(self, prepared: PreparedRequest, dest: Destination, secret: str | None) -> Ack:
        if prepared.body.get("chat_id") not in {str(c) for c in dest.options.get("allowed_chat_ids", [])}:
            raise DeliveryError("recipient_not_allowed", retryable=False)
        if not secret or not TOKEN_RE.match(secret):
            raise DeliveryError("invalid_credential_format", retryable=False)
        resp = self._post_json(f"https://api.telegram.org/bot{secret}/sendMessage", prepared.body)
        try:
            data = json.loads(resp.body or b"{}")
        except ValueError:
            data = {}
        if resp.status == 200 and data.get("ok") is True:
            message_id = (data.get("result") or {}).get("message_id")
            return Ack(provider_ref=str(message_id) if isinstance(message_id, int) else None, status=200)
        retry_after = (data.get("parameters") or {}).get("retry_after")
        if resp.status == 429:
            raise classify_http(resp, retry_after=retry_after if isinstance(retry_after, (int, float)) else None)
        if resp.status == 200:
            raise DeliveryError("unacknowledged", retryable=True, status=200)
        raise classify_http(resp)
