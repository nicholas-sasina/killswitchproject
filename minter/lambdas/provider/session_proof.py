"""Was this request sent through Burn's egress proxy, and for which session?

The proxy adds, on requests it forwards to the provider (and drops any copy the
agent sent itself):

    x-burn-session-proof: v1.<session_id>.<unix_ts>.<hmac>
    hmac = hmac_sha256_hex(fingerprint_key, "burn-session-proof:v1:<session_id>:<unix_ts>")

Agents never hold the fingerprint key, so they cannot forge a proof. No proof,
or a stale/invalid one, means the request came from outside Burn.
"""
import hmac
import time

from burn_contracts.fingerprint import hmac_sha256_hex
from burn_contracts.names import SESSION_ID_RE

HEADER = "x-burn-session-proof"
MAX_AGE_SECONDS = 120


def make(session_id: str, key: bytes, now: float | None = None) -> str:
    """What the proxy sends (here for tests and as the reference implementation)."""
    ts = str(int(time.time() if now is None else now))
    return f"v1.{session_id}.{ts}.{hmac_sha256_hex(key, f'burn-session-proof:v1:{session_id}:{ts}')}"


def verified_session(headers: dict, key: bytes, now: float | None = None) -> str | None:
    """The session id the proxy vouches for, or None."""
    value = {k.lower(): v for k, v in (headers or {}).items()}.get(HEADER)
    if not isinstance(value, str) or len(value) > 300:
        return None
    version, _, rest = value.partition(".")
    rest, _, mac = rest.rpartition(".")
    session_id, _, ts = rest.rpartition(".")
    if version != "v1" or not SESSION_ID_RE.match(session_id) or not ts.isdigit():
        return None
    if abs((time.time() if now is None else now) - int(ts)) > MAX_AGE_SECONDS:
        return None
    expected = hmac_sha256_hex(key, f"burn-session-proof:v1:{session_id}:{ts}")
    return session_id if hmac.compare_digest(expected, mac) else None
