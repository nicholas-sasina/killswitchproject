"""Scrub anything credential-shaped before it reaches a log line or a stored error.

The service never logs provider bodies, URLs or exception messages from HTTP calls in
the first place; this is the backstop for everything else that gets printed.
"""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"https://hooks\.slack\.com/[^\s\"']+"),
    re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[^\s\"']+"),
    re.compile(r"https://[a-z0-9.-]+\.(?:logic\.azure\.com|environment\.api\.powerplatform\.com)[^\s\"']*"),
    re.compile(r"https://api\.telegram\.org/bot[^\s\"']+"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),        # Telegram bot token
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),             # AWS access key id
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),  # JWT
    re.compile(r"\b[0-9a-f]{40,}\b"),                          # long hex (fingerprints, keys)
    re.compile(r"(?i)(sig|signature|token|secret|password|key)=[^&\s\"']+"),
]


def redact(text: object) -> str:
    value = str(text)
    for pattern in _PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def get_logger(name: str = "burn_notify") -> logging.Logger:
    log = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in log.filters):
        log.addFilter(RedactingFilter())
    log.setLevel(logging.INFO)
    return log
