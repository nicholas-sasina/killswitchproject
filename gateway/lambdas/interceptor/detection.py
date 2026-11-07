"""Credential detection for the interceptor, using the shared burn_detect library.

The scanner compares HMAC fingerprints of what it sees against the rows the
minter registered in burn-fingerprints. This module supplies the two things it
needs: the HMAC key (read once per container) and a batched table lookup.
"""
import functools

import boto3
from burn_contracts.fingerprint import key_from_hex
from burn_contracts.names import FINGERPRINT_KEY_SECRET, FINGERPRINTS_TABLE, REGION
from burn_detect import Finding, Scanner

_BATCH = 100  # BatchGetItem limit per request


@functools.cache
def _dynamodb():
    return boto3.resource("dynamodb", region_name=REGION)


def lookup(fingerprints: set[str]) -> dict[str, dict]:
    """burn-fingerprints rows for the fingerprints that exist, keyed by fingerprint."""
    rows, wanted = {}, sorted(fingerprints)
    for start in range(0, len(wanted), _BATCH):
        request = {FINGERPRINTS_TABLE: {"Keys": [{"fingerprint": f} for f in wanted[start:start + _BATCH]]}}
        while request:
            response = _dynamodb().batch_get_item(RequestItems=request)
            for row in response["Responses"].get(FINGERPRINTS_TABLE, []):
                rows[row["fingerprint"]] = row
            request = response.get("UnprocessedKeys") or None
    return rows


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
