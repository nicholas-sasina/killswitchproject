"""Shared helpers for the demo scripts: settings, AWS clients, the minter."""
import json
import os
import sys
from pathlib import Path

import boto3
import httpx

AGENTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENTS_DIR.parent
RUN_DIR = AGENTS_DIR / ".run"
sys.path.insert(0, str(REPO_ROOT / "contracts" / "python"))

from burn_contracts import names  # noqa: E402

REGION = names.REGION


def load_dotenv() -> None:
    """Fill os.environ from a repo-root .env (written by `make env`), without overriding."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def setting(name: str, ssm_path: str) -> str:
    """URLs come from the environment, else from the SSM parameter the stack published."""
    load_dotenv()
    if os.environ.get(name):
        return os.environ[name].rstrip("/")
    value = boto3.client("ssm", region_name=REGION).get_parameter(Name=ssm_path)["Parameter"]["Value"]
    return value.rstrip("/")


def secret_value(secret_id: str) -> str:
    return boto3.client("secretsmanager", region_name=REGION).get_secret_value(SecretId=secret_id)["SecretString"]


def invoke_minter(method: str, path: str, body: dict | None = None) -> dict:
    """Call the minter Lambda with an HTTP-shaped event (see contracts/minter.openapi.yaml)."""
    resp = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=names.MINTER_FUNCTION,
        Payload=json.dumps({"method": method, "path": path, "body": body or {}}).encode(),
    )
    out = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError") or out.get("statusCode", 200) >= 400:
        raise RuntimeError(f"minter {method} {path} failed: {str(out)[:300]}")
    body = out.get("body", {})
    return json.loads(body) if isinstance(body, str) else body


def burn_api(method: str, path: str, **kwargs) -> httpx.Response:
    base = setting("BURN_API_URL", "/burn/api/url")
    token = secret_value(names.DEMO_TOKEN_SECRET)
    headers = {names.DEMO_TOKEN_HEADER: token}
    return httpx.request(method, base + path, headers=headers, timeout=60, **kwargs)
