"""An instance's own credentials: the values Burn returns once, in the reply that starts its session."""
from dataclasses import dataclass, field


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


class MissingSecrets(Exception):
    """The API's reply to POST /sessions carried no `secrets` for this role instance."""

    def __init__(self):
        super().__init__("API too old: no secrets in the join reply")


def from_secrets(secrets: dict | None) -> Credentials:
    """Credentials from the `secrets` field of a POST /sessions reply (badge, and api_key / aws_key if minted)."""
    if not secrets or "badge" not in secrets:
        raise MissingSecrets()
    badge = secrets["badge"]
    aws = secrets.get("aws_key") or {}
    return Credentials(
        Badge(badge["client_id"], badge["client_secret"], badge["token_url"], badge.get("scope", "")),
        api_key=secrets.get("api_key"),
        aws_access_key_id=aws.get("access_key_id"), aws_secret_access_key=aws.get("secret_access_key"))
