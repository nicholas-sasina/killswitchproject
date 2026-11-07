"""Helpers shared by the live scripts (smoke.py, replay_old_key.py).

They talk to the deployed minter and provider using your AWS credentials.
Nothing here prints a secret value: only secret_refs, fingerprint prefixes and status codes.
"""
import json
import urllib.error
import urllib.request

import boto3

from burn_contracts import fingerprint as fp
from burn_contracts.names import FINGERPRINT_KEY_SECRET, MINTER_FUNCTION, REGION

PROVIDER_URL_PARAM = "/burn/provider/url"


def minter(method: str, path: str, body: dict | None = None, query: dict | None = None):
    """Call the burn-minter Lambda; returns (statusCode, body)."""
    event = {"method": method, "path": path, "body": body or {}, "query": query or {}}
    resp = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=MINTER_FUNCTION, Payload=json.dumps(event).encode())
    result = json.loads(resp["Payload"].read())
    return result["statusCode"], result["body"]


def secret_value(secret_ref: str, stage: str = "AWSCURRENT") -> str | None:
    client = boto3.client("secretsmanager", region_name=REGION)
    try:
        return client.get_secret_value(SecretId=secret_ref, VersionStage=stage)["SecretString"]
    except (client.exceptions.ResourceNotFoundException, client.exceptions.InvalidRequestException):
        return None  # missing, or force-deleted and not yet fully gone


def fingerprint_prefix(value: str) -> str:
    """First 8 hex chars of the value's fingerprint: enough to tell keys apart, useless to an attacker."""
    key = fp.key_from_hex(secret_value(FINGERPRINT_KEY_SECRET))
    return fp.fingerprint(value, key)[:8]


def provider_url() -> str:
    ssm = boto3.client("ssm", region_name=REGION)
    return ssm.get_parameter(Name=PROVIDER_URL_PARAM)["Parameter"]["Value"]


def whoami(api_key: str) -> tuple[int, dict]:
    """GET /v1/whoami on the mock provider; returns (status, json body)."""
    request = urllib.request.Request(
        provider_url() + "/v1/whoami", headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as err:
        return err.code, json.load(err)
