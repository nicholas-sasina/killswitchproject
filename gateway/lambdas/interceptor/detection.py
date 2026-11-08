"""Credential detection for the interceptor, using the shared burn_detect library.

The scanner compares HMAC fingerprints of what it sees against the rows the
minter registered in burn-fingerprints. This module supplies the two things it
needs: the HMAC key (read once per container) and the table lookup.

Fingerprint cache: the whole fingerprint set is kept in memory per warm container.
Each call reads one item, names.FINGERPRINTS_VERSION_KEY (bumped by the minter after
every register, rotate and delete), and reloads the set only when that number
changed. So DynamoDB cost per call no longer grows with the size of the tool output.
The version read is consistent, so a credential minted a moment ago is recognized on
the next call. Full reload is fine at this table's size; production would load
incrementally (only the rows changed since the last version).

Fallback: until the minter has bumped the version at least once, or if the cache
cannot be read (e.g. no Scan permission yet), each call looks up its candidates
directly, as before.
"""
import functools
import logging

import boto3
from burn_contracts.fingerprint import key_from_hex
from burn_contracts.names import FINGERPRINT_KEY_SECRET, FINGERPRINTS_TABLE, FINGERPRINTS_VERSION_KEY, REGION
from burn_detect import Finding, Scanner

log = logging.getLogger()
_BATCH = 100  # BatchGetItem limit per request
_cache: dict = {"version": None, "rows": None}


@functools.cache
def _dynamodb():
    return boto3.resource("dynamodb", region_name=REGION)


def reset_cache() -> None:
    _cache.update(version=None, rows=None)


def _version() -> int | None:
    item = _dynamodb().Table(FINGERPRINTS_TABLE).get_item(
        Key={"fingerprint": FINGERPRINTS_VERSION_KEY}, ConsistentRead=True).get("Item")
    return int(item["v"]) if item else None


def _load_all() -> dict[str, dict]:
    rows, kwargs = {}, {"ConsistentRead": True}
    while True:
        page = _dynamodb().Table(FINGERPRINTS_TABLE).scan(**kwargs)
        for row in page["Items"]:
            if row["fingerprint"] != FINGERPRINTS_VERSION_KEY:
                rows[row["fingerprint"]] = row
        if "LastEvaluatedKey" not in page:
            return rows
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def cached_rows() -> dict[str, dict] | None:
    """Every fingerprint row, from memory unless the version changed; None = use the fallback."""
    try:
        version = _version()  # read BEFORE the scan: a bump during the scan reloads next call
        if version is None:
            return None
        if _cache["rows"] is None or version != _cache["version"]:
            _cache.update(rows=_load_all(), version=version)
            log.info("fingerprint cache loaded: version=%s rows=%d", version, len(_cache["rows"]))
        return _cache["rows"]
    except Exception as exc:  # e.g. AccessDenied on Scan before the infra change: stay correct, just costlier
        log.warning("fingerprint cache unavailable (%s); looking up per call", type(exc).__name__)
        return None


def _batch_lookup(fingerprints: set[str]) -> dict[str, dict]:
    rows, wanted = {}, sorted(fingerprints)
    for start in range(0, len(wanted), _BATCH):
        request = {FINGERPRINTS_TABLE: {"Keys": [{"fingerprint": f} for f in wanted[start:start + _BATCH]]}}
        while request:
            response = _dynamodb().batch_get_item(RequestItems=request)
            for row in response["Responses"].get(FINGERPRINTS_TABLE, []):
                rows[row["fingerprint"]] = row
            request = response.get("UnprocessedKeys") or None
    return rows


def lookup(fingerprints: set[str]) -> dict[str, dict]:
    """burn-fingerprints rows for the fingerprints that exist, keyed by fingerprint."""
    rows = cached_rows()
    if rows is None:
        return _batch_lookup(fingerprints)
    return {fp: rows[fp] for fp in fingerprints if fp in rows}


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
