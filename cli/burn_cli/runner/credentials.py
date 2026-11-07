"""Where an instance's own credentials come from.

`burn run` learns only *references* from the API (Secrets Manager names). A CredentialSource turns them into
values on the host, so the agent process is handed its own values and nothing else.
"""
import json
import os
import secrets
from dataclasses import dataclass, field
from typing import Protocol

from .. import retry


@dataclass(frozen=True)
class Badge:
    client_id: str
    client_secret: str = field(repr=False)
    token_url: str = ""
    scope: str = ""


@dataclass(frozen=True)
class Credentials:
    badge: Badge
    api_key: str | None = field(default=None, repr=False)
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = field(default=None, repr=False)


class CredentialSource(Protocol):
    def fetch(self, session: dict) -> Credentials:
        """Credentials for a POST /sessions response (badge_ref and credentials refs)."""


class SecretsManagerSource:
    """Reads an instance's secrets from AWS Secrets Manager with the host's own AWS access.

    The client is injectable so tests can pass a moto-backed one. Secrets that were created a moment ago can
    be missing for a short time, and IAM changes take seconds to apply, so reads retry with backoff.
    """

    def __init__(self, client=None, region: str = "us-east-1", sleep=None, attempts: int = 6):
        self._client, self._region, self._attempts = client, region, attempts
        self._retry = {"sleep": sleep} if sleep else {}

    @property
    def client(self):
        if self._client is None:
            import boto3  # imported late: commands that never fetch credentials do not need AWS
            self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def _get(self, secret_id: str) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        def transient(exc: Exception) -> bool:
            if isinstance(exc, ClientError):
                return exc.response["Error"]["Code"] in {
                    "ResourceNotFoundException", "ThrottlingException", "InternalServiceError",
                    "AccessDeniedException"}  # a brand-new grant can take seconds to apply
            return isinstance(exc, BotoCoreError)

        return retry.call(lambda: self.client.get_secret_value(SecretId=secret_id)["SecretString"],
                          attempts=self._attempts, retry_if=transient, **self._retry)

    def fetch(self, session: dict) -> Credentials:
        badge = json.loads(self._get(session["badge_ref"]))
        values: dict = {}
        for ref in session.get("credentials", []):
            raw = self._get(ref["secret_ref"])
            if ref["type"] == "api_key":
                values["api_key"] = raw
            elif ref["type"] == "aws_key":
                key = json.loads(raw)
                values["aws_access_key_id"] = key["access_key_id"]
                values["aws_secret_access_key"] = key["secret_access_key"]
        return Credentials(
            Badge(badge["client_id"], badge["client_secret"], badge["token_url"], badge.get("scope", "")), **values)


class FakeSource:
    """Credentials made up on the spot, for tests and for running against the mock servers.

    The badge's client id is the agent id and `token_url` points at a badge issuer (agents/scripts/
    mock_badge_issuer.py), which is what the MCP mock expects. Nothing here is a real secret.
    """

    def __init__(self, token_url: str = "", with_aws: bool = True):
        self.token_url, self.with_aws = token_url, with_aws
        self.sessions: list[dict] = []

    def fetch(self, session: dict) -> Credentials:
        self.sessions.append(session)
        types = {c["type"] for c in session.get("credentials", [])}
        return Credentials(
            Badge(session["agent_id"], "fake-" + secrets.token_hex(8), self.token_url),
            api_key="mpk_fake_" + secrets.token_hex(8) if "api_key" in types else None,
            aws_access_key_id="AKIAFAKE" + secrets.token_hex(6).upper() if self.with_aws and "aws_key" in types else None,
            aws_secret_access_key="fake-" + secrets.token_hex(16) if self.with_aws and "aws_key" in types else None)


def from_name(name: str) -> CredentialSource:
    """`aws` (default) reads Secrets Manager; `fake` is for development against the mock servers."""
    if name in ("", "aws"):
        return SecretsManagerSource()
    if name == "fake":
        return FakeSource(os.environ.get("BURN_MOCK_BADGE_URL", "http://127.0.0.1:8702"))
    raise ValueError(f"unknown credential source {name!r} (use aws or fake)")
