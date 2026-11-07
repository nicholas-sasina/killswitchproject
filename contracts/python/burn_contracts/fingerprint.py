"""Credential fingerprints: the only implementation, imported by every lane.

We never store raw credential values outside Secrets Manager. Instead we
store HMAC-SHA256(server_key, value). Without the server key a fingerprint
cannot be brute-forced back to the value, even for short fragments.

Two kinds:
- exact:   HMAC of the whole value. Catches verbatim copies (after decoding).
- shingle: HMAC of every 8-char window of the secret's random body,
           lowercased. Catches fragments, case changes, and a key split
           across several requests (by adding up which windows were seen).
"""
import hashlib
import hmac

SHINGLE_K = 8
_SHINGLE_PREFIX = "sh:"


def hmac_sha256_hex(key: bytes, message: str) -> str:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()


def fingerprint(value: str, key: bytes) -> str:
    """Exact fingerprint of a credential value."""
    return hmac_sha256_hex(key, value)


def shingle_windows(body: str, k: int = SHINGLE_K) -> list[str]:
    """Every k-char window of body.lower(), in order (index = position)."""
    text = body.lower()
    if len(text) < k:
        return []
    return [text[i : i + k] for i in range(len(text) - k + 1)]


def shingle_fingerprint(window: str, key: bytes) -> str:
    """Fingerprint of one shingle window (already lowercased, length k)."""
    return hmac_sha256_hex(key, _SHINGLE_PREFIX + window)


def shingle_fingerprints(body: str, key: bytes, k: int = SHINGLE_K) -> list[tuple[int, str]]:
    """(index, fingerprint) for every window. Used by the minter to register a credential."""
    return [(i, shingle_fingerprint(w, key)) for i, w in enumerate(shingle_windows(body, k))]


def key_from_hex(hex_key: str) -> bytes:
    """Decode the burn-system/fingerprint-key secret value."""
    key = bytes.fromhex(hex_key.strip())
    if len(key) < 16:
        raise ValueError("fingerprint key too short")
    return key
