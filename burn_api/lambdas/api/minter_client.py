"""Calls the credential minter. It is a Lambda with an HTTP-shaped event (see contracts/minter.openapi.yaml)."""
import json

import boto3
from burn_contracts import names

_lambda = None


class MinterError(Exception):
    def __init__(self, message: str, status: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.status, self.detail = status, detail  # minter's status and validation message, if any


def call(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda", region_name=names.REGION)
    payload = {"method": method, "path": path, "body": body or {}, "query": query or {}}
    resp = _lambda.invoke(FunctionName=names.MINTER_FUNCTION, Payload=json.dumps(payload).encode())
    out = json.loads(resp["Payload"].read() or b"{}")
    body = out.get("body", {})
    body = json.loads(body) if isinstance(body, str) else body
    if resp.get("FunctionError") or out.get("statusCode") != 200:
        raise MinterError(f"minter {method} {path} failed: {out.get('statusCode', 'function error')}",
                          status=out.get("statusCode"), detail=(body or {}).get("error"))
    return body
