"""Provider registry. Register a new destination type here; nothing else changes."""
from __future__ import annotations

from .base import Ack, DeliveryError, Destination, PreparedRequest, Provider
from .discord import DiscordProvider
from .email import EmailProvider
from .slack import SlackProvider
from .teams import TeamsProvider
from .telegram import TelegramProvider

REGISTRY: dict[str, type[Provider]] = {
    "slack": SlackProvider,
    "telegram": TelegramProvider,
    "discord": DiscordProvider,
    "teams": TeamsProvider,
    "email": EmailProvider,
}

__all__ = ["REGISTRY", "Ack", "DeliveryError", "Destination", "PreparedRequest", "Provider"]
