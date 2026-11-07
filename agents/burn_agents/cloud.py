"""Local tools that use the agent's OWN credentials.

They exist so the agent's AWS key and API key show up in CloudTrail and in
the provider's logs well before any burn; that history is the blast radius.
"""
import time

import boto3
import httpx
from botocore.exceptions import ClientError


def aws_session(access_key_id: str, secret_access_key: str) -> boto3.Session:
    # Fail closed: explicit keys only, never the default credential chain. With no
    # key of its own an agent would silently run as the host, so refuse to start.
    # Burning (rotating) these keys then also cuts the agent off from the model.
    if not access_key_id or not secret_access_key:
        raise SystemExit("this agent has no AWS key of its own; refusing to fall back to other credentials")
    return boto3.Session(
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="us-east-1",
    )


def check_cloud_access(session: boto3.Session) -> str:
    """Who am I in AWS, and which buckets can I see? Returns a short summary."""
    arn = session.client("sts").get_caller_identity()["Arn"]
    buckets = session.client("s3").list_buckets().get("Buckets", [])
    return f"identity {arn.split('/')[-1]}, {len(buckets)} buckets visible"


def check_provider(provider_url: str, api_key: str) -> str:
    """Ask the mock API provider who this key belongs to (GET /v1/whoami)."""
    resp = httpx.get(provider_url.rstrip("/") + "/v1/whoami",
                     headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
    return f"provider answered HTTP {resp.status_code}"


NOT_YET_ACTIVE = {"InvalidClientTokenId", "InvalidAccessKeyId"}
RETRY_DELAYS = (1, 2, 4, 5, 5, 5)  # about 20 s in total


def retry_while_key_activates(call, sleep=time.sleep):
    """A freshly created IAM key needs a few seconds before AWS accepts it."""
    for delay in RETRY_DELAYS:
        try:
            return call()
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in NOT_YET_ACTIVE:
                raise
            sleep(delay)
    return call()
