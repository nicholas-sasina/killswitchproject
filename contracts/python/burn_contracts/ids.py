"""Event ids: "evt_" + ULID.

A ULID starts with a 48-bit millisecond timestamp, so ids sort by creation
time as plain strings. That is what lets the dashboard poll
GET /events?since=<last event_id> with a simple "greater than" query.
"""
import os
import time

# Crockford base32 (no I, L, O, U), the standard ULID alphabet.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(now_ms: int | None = None) -> str:
    """26-char ULID: 10 chars of time + 16 chars of randomness."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ms, 10) + _encode(rand, 16)


def new_event_id(now_ms: int | None = None) -> str:
    return "evt_" + new_ulid(now_ms)
