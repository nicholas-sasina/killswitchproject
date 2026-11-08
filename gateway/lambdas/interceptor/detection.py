"""Credential detection for the interceptor, backed by a short-lived fingerprint cache.

The scanner compares HMAC fingerprints of observed text against rows registered
by the minter in burn-fingerprints. The HMAC key is read once per Lambda
container. Fingerprint rows are loaded into an in-memory snapshot and refreshed
periodically, rather than issuing BatchGetItem calls for every candidate in
every tool response.

The snapshot contains fingerprints and metadata only; it never contains raw
credential values.
"""
import functools
import threading
import time

import boto3
from burn_contracts.fingerprint import key_from_hex
from burn_contracts.names import FINGERPRINT_KEY_SECRET, FINGERPRINTS_TABLE, REGION
from burn_detect import Finding, Scanner

# Keep this short so newly issued/rotated credentials become visible quickly.
# A warm interceptor performs no fingerprint-table reads between refreshes.
_CACHE_TTL_SECONDS = 5.0

@functools.cache
def _dynamodb():
    return boto3.resource("dynamodb", region_name=REGION)


_cache_lock = threading.Lock()
_fingerprint_rows: dict[str, dict] | None = None
_fingerprint_rows_loaded_at = 0.0


def refresh_cache(*, force: bool = True) -> int:
    """Refresh the in-memory fingerprint snapshot from DynamoDB.

    A strongly consistent paginated Scan makes each published snapshot
    internally useful immediately after a minter write. This is intentionally
    a periodic table read, not a per-output/per-candidate read.
    Returns the number of fingerprint rows cached.
    """
    global _fingerprint_rows, _fingerprint_rows_loaded_at

    with _cache_lock:
        now = time.monotonic()
        if (not force and _fingerprint_rows is not None
                and now - _fingerprint_rows_loaded_at < _CACHE_TTL_SECONDS):
            return len(_fingerprint_rows)

        table = _dynamodb().Table(FINGERPRINTS_TABLE)
        rows: dict[str, dict] = {}
        kwargs = {"ConsistentRead": True}
        while True:
            response = table.scan(**kwargs)
            for row in response.get("Items", []):
                digest = row.get("fingerprint")
                if isinstance(digest, str):
                    rows[digest] = row
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        _fingerprint_rows = rows
        _fingerprint_rows_loaded_at = time.monotonic()
        return len(rows)


def reset_cache() -> None:
    """Clear the snapshot (tests and explicit lifecycle reset only)."""
    global _fingerprint_rows, _fingerprint_rows_loaded_at
    with _cache_lock:
        _fingerprint_rows = None
        _fingerprint_rows_loaded_at = 0.0


def lookup(fingerprints: set[str]) -> dict[str, dict]:
    """Return cached rows for matching candidates; no DynamoDB read per call."""
    if not fingerprints:
        return {}

    now = time.monotonic()
    if (_fingerprint_rows is None
            or now - _fingerprint_rows_loaded_at >= _CACHE_TTL_SECONDS):
        refresh_cache(force=False)

    # Snapshots are replaced atomically, so readers can safely retain this
    # local reference while another invocation refreshes the module cache.
    snapshot = _fingerprint_rows or {}
    return {digest: snapshot[digest] for digest in fingerprints if digest in snapshot}


@functools.cache
def scanner() -> Scanner:
    secret = boto3.client("secretsmanager", region_name=REGION).get_secret_value(SecretId=FINGERPRINT_KEY_SECRET)
    return Scanner(key=key_from_hex(secret["SecretString"]), lookup=lookup)


def scan(text: str) -> list[Finding]:
    """Findings in text. Raises ValueError if the text is too large to check."""
    return scanner().scan(text) if text else []


def ledger_key(finding: Finding) -> str:
    """burn-exposures sort key: the secret_ref, or fp#<fingerprint> for unknown-format credentials."""
    return finding.secret_ref or f"fp#{finding.fingerprint}"


def by_credential(findings: list[Finding]) -> dict[str, Finding]:
    """One finding per credential. A credential can match on several parts
    (e.g. an AWS key's id and secret); exact matches win over shingle matches."""
    best: dict[str, Finding] = {}
    for finding in findings:
        key = ledger_key(finding)
        if key not in best or (finding.kind == "exact" and best[key].kind != "exact"):
            best[key] = finding
    return best
