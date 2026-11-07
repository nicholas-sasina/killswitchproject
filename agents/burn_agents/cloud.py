"""Local tools that use the agent's OWN credentials.

They exist so the agent's AWS key and API key show up in CloudTrail and in
the provider's logs well before any burn; that history is the blast radius.
"""
import boto3
import httpx


def aws_session(access_key_id: str, secret_access_key: str) -> boto3.Session:
    # Explicit keys, never the default chain: the agent can only use what it
    # was issued, so burning (rotating) those keys also cuts it off from the model.
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
