"""The instance's badge: an OAuth access token minted with client credentials on the host.

The token is cached and refreshed before it expires. The client secret never leaves this process; the agent
only ever causes calls to carry `Authorization: Bearer <token>` through the local MCP endpoint.
"""
import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from .credentials import Badge

REFRESH_MARGIN = 60.0  # seconds before expiry


class TokenRefused(Exception):
    """The badge issuer will not give this instance a token any more (burned or revoked)."""


class TokenUnavailable(Exception):
    """The issuer could not be reached or failed; a later attempt may work."""


def _post(url: str, data: dict, user: str, password: str) -> dict:
    basic = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, method="POST", data=urllib.parse.urlencode(data).encode(), headers={
        "authorization": f"Basic {basic}", "content-type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        if err.code in (400, 401, 403):
            raise TokenRefused(f"badge issuer refused the client (HTTP {err.code})") from None
        raise TokenUnavailable(f"badge issuer failed (HTTP {err.code})") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise TokenUnavailable(f"cannot reach the badge issuer ({getattr(exc, 'reason', exc)})") from None


class BadgeToken:
    def __init__(self, badge: Badge, post: Callable[..., dict] = _post, clock: Callable[[], float] = time.monotonic):
        self._badge, self._post, self._clock = badge, post, clock
        self._token, self._expires_at = "", 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if not self._token or self._clock() >= self._expires_at - REFRESH_MARGIN:
                self._mint()
            return self._token

    def _mint(self) -> None:
        data = {"grant_type": "client_credentials"}
        if self._badge.scope:
            data["scope"] = self._badge.scope
        body = self._post(self._badge.token_url, data, self._badge.client_id, self._badge.client_secret)
        self._token = body["access_token"]
        self._expires_at = self._clock() + float(body.get("expires_in", 3600))
