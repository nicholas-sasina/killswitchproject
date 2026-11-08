"""Who may call which route.

- The admin (demo) token in the x-burn-demo-token header may call every route.
- A join needs no admin token: POST /sessions with exactly {role, enroll_token} carries its own authority, the
  join token, which the route checks (hash, expiry, uses left, revocation). Any other body shape on that route,
  for example one that also names a parent_session or an agent_id, needs the admin token.

Everything else needs the admin token.
"""
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


def is_admin(headers: dict) -> bool:
    given = headers.get(names.DEMO_TOKEN_HEADER)
    if not given:
        return False  # no Secrets Manager call for a request that does not claim to be the admin
    # compare_digest takes the same time wherever the strings differ.
    return hmac.compare_digest(given.encode(), _expected())


def is_join(method: str, path: str, body: dict) -> bool:
    """A join with a well-formed join token and nothing else. The shape check costs no lookup."""
    return (method == "POST" and path == "/sessions" and set(body) == {"role", "enroll_token"}
            and isinstance(body["role"], str) and isinstance(body["enroll_token"], str)
            and names.JOIN_TOKEN_RE.fullmatch(body["enroll_token"]) is not None)  # "$" would allow a "\n"


def allows(method: str, path: str, headers: dict, body: dict) -> bool:
    return is_admin(headers) or is_join(method, path, body)


def reset():
    global _token
    _token = None
