"""Who may call which route.

- The admin (demo) token in the x-burn-demo-token header may call every route.
- A join needs no admin token: POST /sessions with exactly {role, enroll_token} carries its own authority, the
  join token, which the route checks (hash, expiry, uses left, revocation). Any other body shape on that route,
  for example one that also names a parent_session or an agent_id, needs the admin token.
- An instance's own calls accept its session token (issued once in its join reply) as
  `Authorization: Bearer bst_...`: POST /instances/{id}/heartbeat and /exit for that instance, and
  POST /sessions with exactly {role, parent_session} to spawn a child of it. Auth only proves whose token it
  is; the routes still decide on the session's status (a burned instance's heartbeat gets 409, its spawn 403).

Everything else needs the admin token.
"""
import hashlib
import hmac

import boto3
from burn_contracts import names

import db

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


def instance_session(method: str, path: str, body: dict) -> str | None:
    """The session whose token may authorize this request, or None when the route is not instance-scoped.

    Parsed the way the router parses it, so auth and the route always agree on which session is meant.
    """
    if method != "POST":
        return None
    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "instances" and parts[2] in ("heartbeat", "exit"):
        return parts[1]
    if path == "/sessions" and set(body) == {"role", "parent_session"} and isinstance(body["role"], str) \
            and isinstance(body["parent_session"], str):
        return body["parent_session"]
    return None


def is_session_owner(headers: dict, session_id: str) -> bool:
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not names.is_session_token(token) or not names.SESSION_ID_RE.fullmatch(session_id):
        return False  # shape checks first: a malformed request costs no lookup
    row = db.table(names.SESSIONS_TABLE).get_item(
        Key={"session_id": session_id}, ConsistentRead=True,  # a heartbeat may follow the join at once
        ProjectionExpression="session_token_sha256").get("Item") or {}
    stored = row.get("session_token_sha256")
    return isinstance(stored, str) and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), stored)


def allows(method: str, path: str, headers: dict, body: dict) -> bool:
    if is_admin(headers) or is_join(method, path, body):
        return True
    session_id = instance_session(method, path, body)
    return session_id is not None and is_session_owner(headers, session_id)


def reset():
    global _token
    _token = None
