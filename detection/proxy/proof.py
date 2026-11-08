"""Short-lived provider origin proof, signed only after proxy authorization."""
import time

from burn_contracts.fingerprint import hmac_sha256_hex
from burn_contracts.names import SESSION_ID_RE

HEADER = "x-burn-session-proof"


def make_session_proof(session_id: str, key: bytes, *, now: float | None = None) -> str:
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("invalid session id for provider proof")
    ts = str(int(time.time() if now is None else now))
    mac = hmac_sha256_hex(key, f"burn-session-proof:v1:{session_id}:{ts}")
    return f"v1.{session_id}.{ts}.{mac}"
