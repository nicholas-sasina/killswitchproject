"""Calls the credential minter. It is a Lambda with an HTTP-shaped event (see contracts/minter.openapi.yaml)."""
import json

import boto3
from burn_contracts import names

_lambda = None


class MinterError(Exception):
    pass


def call(method: str, path: str, body: dict | None = None) -> dict:
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda", region_name=names.REGION)
    payload = {"method": method, "path": path, "body": body or {}}
    resp = _lambda.invoke(FunctionName=names.MINTER_FUNCTION, Payload=json.dumps(payload).encode())
    out = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError") or out.get("statusCode") != 200:
        raise MinterError(f"minter {method} {path} failed: {out.get('statusCode', 'function error')}")
    body = out.get("body", {})
    return json.loads(body) if isinstance(body, str) else body
