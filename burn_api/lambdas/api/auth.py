"""Who may call which route.

Two modes, chosen by the deployment (names.PEOPLE_AUTH_ENV, set by infra's people sign-in flag):

Sign-in off (BURN_PEOPLE_AUTH unset), as before sign-in existed:
- The admin (demo) token in the x-burn-demo-token header may call every route.
- A join needs no admin token: POST /sessions or POST /instances with exactly {role, enroll_token} carries its own
  authority, the join token, which the route checks (hash, expiry, uses left, revocation). Any other body shape on
  that route, for example one that also names a parent_session or an agent_id, needs the admin token.
- An instance's own calls accept its session token (issued once in its join reply) as
  `Authorization: Bearer bst_...`: POST /instances/{id}/heartbeat and /exit for that instance, and POST /sessions or
  POST /instances with exactly {role, parent_session} to spawn a child of it. Auth only proves whose token it is;
  the routes still decide on the session's status (a burned instance's heartbeat gets 409, its spawn 403).

Sign-in on (BURN_PEOPLE_AUTH=1):
- Every route except the three exempt ones needs a signed-in person: the claims API Gateway's JWT authorizer put in
  requestContext.authorizer.jwt.claims. They are only ever read from there, never from a header or the body, and
  this check does not rely on the authorizer having run.
- The exempt routes are POST /instances, POST /instances/{id}/heartbeat and POST /instances/{id}/exit. They take the
  join token or the instance's session token exactly as above. (POST /sessions is not exempt: it needs a person.)
- The admin token is honored only when the API also runs with BURN_LOCAL_DEV=1, which a deployment never sets.

In both modes the caller is returned so routes can record who did what (see caller.py).
"""
import hashlib
import hmac
import os

import boto3
from burn_contracts import names

import caller
import db

# The two paths that start a role instance. A gateway can then authorize them by path alone.
START_PATHS = ("/sessions", "/instances")

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
    return (method == "POST" and path in START_PATHS and set(body) == {"role", "enroll_token"}
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
    if path in START_PATHS and set(body) == {"role", "parent_session"} and isinstance(body["role"], str) \
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


def people_auth_on() -> bool:
    return os.environ.get(names.PEOPLE_AUTH_ENV) == "1"


def local_dev() -> bool:
    return os.environ.get(names.LOCAL_DEV_ENV) == "1"


def is_exempt(method: str, path: str) -> bool:
    """The routes that carry their own credential, so sign-in does not apply to them."""
    if method != "POST":
        return False
    parts = path.strip("/").split("/")
    return parts == ["instances"] or (len(parts) == 3 and parts[0] == "instances" and parts[2] in ("heartbeat", "exit"))


def person(event: dict) -> str | None:
    """The signed-in person's email, from the JWT authorizer's claims in requestContext, or None.

    API Gateway writes requestContext; a client cannot. A token without an email is not a person.
    """
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims")
    if not isinstance(claims, dict):
        return None
    email = claims.get("email")
    return email if isinstance(email, str) and "@" in email and claims.get("sub") else None


def _own_credential(method: str, path: str, headers: dict, body: dict) -> caller.Caller | None:
    """A join token or an instance's session token, for the routes that accept one."""
    if is_join(method, path, body):
        return caller.Caller(actor=None, person=None)  # the new instance does not exist yet
    session_id = instance_session(method, path, body)
    if session_id is not None and is_session_owner(headers, session_id):
        return caller.Caller(actor=f"instance:{session_id}", person=None)
    return None


def authenticate(event: dict, method: str, path: str, headers: dict, body: dict) -> caller.Caller | None:
    """Who is calling, or None when the request may not call this route."""
    email = person(event)
    if email:
        return caller.Caller(actor=email, person=email)
    if not people_auth_on():
        if is_admin(headers):
            return caller.Caller(actor="admin", person=None)
        return _own_credential(method, path, headers, body)
    if local_dev() and is_admin(headers):
        return caller.Caller(actor="admin", person=None)
    if is_exempt(method, path):
        return _own_credential(method, path, headers, body)
    return None  # a non-exempt route with no person


def allows(method: str, path: str, headers: dict, body: dict, event: dict | None = None) -> bool:
    return authenticate(event or {}, method, path, headers, body) is not None


def reset():
    global _token
    _token = None
