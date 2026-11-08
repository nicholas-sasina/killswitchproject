"""Calls the credential minter. It is a Lambda with an HTTP-shaped event (see contracts/minter.openapi.yaml)."""
import json
import threading

import boto3
from burn_contracts import names

import errors

_lambda = None
_lock = threading.Lock()


def _client():
    # Built under a lock: a scoped burn calls the minter from many threads at once, and a cold
    # container must create the boto3 client once, not from every thread.
    global _lambda
    if _lambda is None:
        with _lock:
            if _lambda is None:
                _lambda = boto3.client("lambda", region_name=names.REGION)
    return _lambda


class MinterError(Exception):
    pass


class MinterThrottled(MinterError, errors.Throttled):
    """The minter hit a provider rate limit (HTTP 429 in its response)."""


def call(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    payload = {"method": method, "path": path, "body": body or {}, "query": query or {}}
    resp = _client().invoke(FunctionName=names.MINTER_FUNCTION, Payload=json.dumps(payload).encode())
    out = json.loads(resp["Payload"].read() or b"{}")
    if out.get("statusCode") == 429:
        raise MinterThrottled(f"minter {method} {path} is rate limited")
    if resp.get("FunctionError") or out.get("statusCode") != 200:
        raise MinterError(f"minter {method} {path} failed: {out.get('statusCode', 'function error')}")
    body = out.get("body", {})
    return json.loads(body) if isinstance(body, str) else body


def call_async(method: str, path: str, body: dict | None = None) -> None:
    """Fire-and-forget minter call: Lambda queues it (and retries it on failure) and returns at once.

    Only for idempotent cleanup the caller does not need the answer to. If queuing itself fails the
    exception propagates, so the caller can leave the work to be retried.
    """
    payload = {"method": method, "path": path, "body": body or {}, "query": {}}
    resp = _client().invoke(FunctionName=names.MINTER_FUNCTION, InvocationType="Event",
                          Payload=json.dumps(payload).encode())
    if resp.get("StatusCode") != 202:
        raise MinterError(f"minter {method} {path} was not queued")
