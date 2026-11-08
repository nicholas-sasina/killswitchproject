"""Per-channel escaping.

Message text is fixed policy wording plus contract identifiers that already passed
strict patterns, so there is normally nothing to escape. These functions are still
applied to every interpolated value as defense in depth: a value can never open a
link, a mention, a command or a formatting span.
"""
from __future__ import annotations

import html
import re

ZWSP = "​"
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")
_MENTION_RE = re.compile(r"@(?=\S)")
_DISCORD_MD_RE = re.compile(r"([\\*_~`|>#\-\[\]()])")
_TEAMS_MD_RE = re.compile(r"([\\*_~`\[\]()])")
_TEAMS_BLOCK_RE = re.compile(r"^([#\->+]|\d+\.)")


def clean(text: object, limit: int = 300) -> str:
    """Drop control and bidi-override characters, collapse whitespace, bound length."""
    value = _CONTROL_RE.sub("", str(text))
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _defuse_mentions(text: str) -> str:
    return _MENTION_RE.sub("@" + ZWSP, text)


def slack(text: object, limit: int = 300) -> str:
    """Slack mrkdwn: &, <, > are the only control characters (they form <!here>, <@U..>, <url|x>)."""
    value = clean(text, limit).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _defuse_mentions(value)


def telegram_html(text: object, limit: int = 300) -> str:
    """Telegram parse_mode=HTML."""
    return _defuse_mentions(html.escape(clean(text, limit), quote=True))


def discord(text: object, limit: int = 300) -> str:
    """Discord markdown; mentions are also disabled through allowed_mentions."""
    value = _DISCORD_MD_RE.sub(r"\\\1", clean(text, limit))
    value = value.replace("<", "<" + ZWSP)  # <@id>, <#id>, <t:..>, <:emoji:>
    return _defuse_mentions(value)


def teams(text: object, limit: int = 300) -> str:
    """Adaptive Card markdown subset (bold, italic, links, lists). Values are single-line, so
    block syntax (#, -, +, >, "1.") only matters at the start, where a ZWSP defuses it."""
    value = _TEAMS_MD_RE.sub(r"\\\1", clean(text, limit))
    if _TEAMS_BLOCK_RE.match(value):
        value = ZWSP + value
    return _defuse_mentions(value.replace("<", "&lt;"))


def email_html(text: object, limit: int = 300) -> str:
    return html.escape(clean(text, limit), quote=True)
