"""Join tokens: bjt_<token_id>.<secret>. Only sha256(secret) is stored.

A token is redeemed with one conditional DynamoDB update, so any number of parallel joins can
never use it more times than it allows. A token value is never logged or put in an event.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError
from burn_contracts import names
from burn_contracts.events import utc_now_iso

import caller
import db
import errors
import roles

MAX_USES, MAX_TTL, MIN_TTL = 10000, 2592000, 60
_NO_SUCH_TOKEN = hashlib.sha256(b"no such token").hexdigest()  # compared when the id is unknown


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _int_in(body: dict, key: str, default: int, low: int, high: int) -> int:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise errors.ApiError(400, f"{key} must be an integer from {low} to {high}")
    return value


def view(row: dict) -> dict:
    uses_left = int(row["uses_left"])
    status = ("revoked" if row["revoked"] else "expired" if row["expires_at"] <= utc_now_iso()
              else "used_up" if uses_left <= 0 else "active")
    return {"token_id": row["token_id"], "role": row["role"], "uses": int(row["uses"]),
            "uses_left": uses_left, "expires_at": row["expires_at"], "created_at": row["created_at"],
            "created_by": row.get("created_by"), "status": status}


def create(role_name: str, body: dict) -> dict:
    if not names.is_role_name(role_name):
        raise errors.ApiError(400, "bad role name")
    roles.lookup(role_name)
    uses = _int_in(body, "uses", 1, 1, MAX_USES)
    ttl = _int_in(body, "ttl", 3600, MIN_TTL, MAX_TTL)
    token_id, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
    row = {
        "token_id": token_id, "role": role_name, "secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
        "uses": uses, "uses_left": uses, "revoked": False,
        "expires_at": _iso(datetime.now(timezone.utc) + timedelta(seconds=ttl)), "created_at": utc_now_iso(),
        "created_by": caller.get().person,
    }
    db.table(names.ENROLL_TOKENS_TABLE).put_item(Item=row)
    return view(row) | {"token": f"{names.JOIN_TOKEN_PREFIX}{token_id}.{secret}"}


def get(token_id: str, consistent: bool = False) -> dict:
    row = db.table(names.ENROLL_TOKENS_TABLE).get_item(
        Key={"token_id": token_id}, ConsistentRead=consistent).get("Item")
    if row is None:
        raise errors.ApiError(404, "Unknown token")
    return row


def revoke(token_id: str) -> dict:
    get(token_id)
    row = db.table(names.ENROLL_TOKENS_TABLE).update_item(
        Key={"token_id": token_id}, UpdateExpression="SET revoked = :t",
        ExpressionAttributeValues={":t": True}, ReturnValues="ALL_NEW")["Attributes"]
    return view(row)


def redeem(role_name: str, token: str) -> str:
    """Use one of the token's uses for `role_name` and return the token id, or raise 403."""
    invalid = errors.ApiError(403, "Invalid join token")
    parsed = names.parse_join_token(token if isinstance(token, str) else "")
    if not parsed:
        raise invalid
    token_id, secret = parsed
    row = db.table(names.ENROLL_TOKENS_TABLE).get_item(Key={"token_id": token_id}).get("Item")
    # Hash and compare even for an unknown id, so timing does not reveal which ids exist.
    expected = row["secret_sha256"] if row else _NO_SUCH_TOKEN
    matches = hmac.compare_digest(hashlib.sha256(secret.encode()).hexdigest(), expected)
    if not row or not matches:
        raise invalid
    if row["role"] != role_name:
        raise errors.ApiError(403, "Join token is for a different role")
    try:
        db.table(names.ENROLL_TOKENS_TABLE).update_item(
            Key={"token_id": token_id}, UpdateExpression="SET uses_left = uses_left - :one",
            ConditionExpression="uses_left > :zero AND revoked = :no AND expires_at > :now",
            ExpressionAttributeValues={":one": 1, ":zero": 0, ":no": False, ":now": utc_now_iso()},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        raise errors.ApiError(403, f"Join token {view(get(token_id))['status']}") from None
    return token_id


def refund(token_id: str):
    """Give back a use when the join failed after the token was redeemed."""
    try:
        db.table(names.ENROLL_TOKENS_TABLE).update_item(
            Key={"token_id": token_id}, UpdateExpression="SET uses_left = uses_left + :one",
            ConditionExpression="uses_left < uses", ExpressionAttributeValues={":one": 1})
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
