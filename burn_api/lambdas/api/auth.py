"""Demo-token check. Every route requires the x-burn-demo-token header."""
import hmac

import boto3
from burn_contracts import names

_token: bytes | None = None


def _expected() -> bytes:
    # Cached per Lambda container: one Secrets Manager call per cold start.
    global _token
    if _token is None:
        client = boto3.client("secretsmanager", region_name=names.REGION)
        _token = client.get_secret_value(SecretId=names.DEMO_TOKEN_SECRET)["SecretString"].encode()
    return _token


def is_authorized(headers: dict) -> bool:
    given = (headers.get(names.DEMO_TOKEN_HEADER) or "").encode()
    # compare_digest takes the same time wherever the strings differ.
    return hmac.compare_digest(given, _expected())


def reset():
    global _token
    _token = None
