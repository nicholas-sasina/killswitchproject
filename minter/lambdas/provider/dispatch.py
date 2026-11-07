"""Hand work to another Burn Lambda by direct invoke. Tests replace invoke()."""
import functools
import json
import os

import boto3
from burn_contracts.names import BURN_API_FUNCTION, MINTER_FUNCTION, REGION


@functools.cache
def _lambda():
    return boto3.client("lambda", region_name=REGION)


def burn_api_function() -> str:
    return os.environ.get("BURN_API_FUNCTION", BURN_API_FUNCTION)


def minter_function() -> str:
    return os.environ.get("MINTER_FUNCTION", MINTER_FUNCTION)


def invoke(function_name: str, payload: dict, wait: bool) -> dict | None:
    """wait=False: queue it (Lambda retries async invokes itself) and return None.
    wait=True: return the function's JSON response."""
    resp = _lambda().invoke(FunctionName=function_name, Payload=json.dumps(payload).encode(),
                            InvocationType="RequestResponse" if wait else "Event")
    if not wait:
        return None
    if resp.get("FunctionError"):
        raise RuntimeError(f"{function_name} failed")
    return json.loads(resp["Payload"].read() or b"{}")
