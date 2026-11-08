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
    """The join reply carried no `secrets`, or no session token, for this role instance."""

    def __init__(self, what: str = "secrets"):
        super().__init__(f"API too old: no {what} in the join reply")


def session_token_of(secrets: dict | None) -> str:
    """The instance's own session token (bst_...). It authenticates the supervisor's calls and goes nowhere else."""
    token = (secrets or {}).get("session_token")
    if not token:
        raise MissingSecrets("session token")
    return token


def from_secrets(secrets: dict | None) -> Credentials:
    """Credentials for the agent from a join reply's `secrets` (badge, and api_key / aws_key if minted).

    The session token is deliberately not part of them: the agent never gets it.
    """
    if not secrets or "badge" not in secrets:
        raise MissingSecrets()
    badge = secrets["badge"]
    aws = secrets.get("aws_key") or {}
    return Credentials(
        Badge(badge["client_id"], badge["client_secret"], badge["token_url"], badge.get("scope", "")),
        api_key=secrets.get("api_key"),
        aws_access_key_id=aws.get("access_key_id"), aws_secret_access_key=aws.get("secret_access_key"))
