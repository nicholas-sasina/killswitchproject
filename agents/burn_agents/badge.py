"""The agent's badge: an OAuth access token minted with client credentials.

We mint once, reuse the token, and refresh a little before it expires. If
the issuer refuses (the badge was revoked, the client deleted, the secret
rotated) we raise TokenRefused, which the loop treats as "stop cleanly".
"""
import time
from typing import Callable

import httpx

REFRESH_MARGIN_SECONDS = 60


class TokenRefused(Exception):
    """The badge issuer will not give this agent a token any more."""


class BadgeToken:
    def __init__(self, token_url: str, client_id: str, client_secret: str, scope: str = "",
                 post: Callable = httpx.post, clock: Callable[[], float] = time.monotonic):
        self._url, self._id, self._secret, self._scope = token_url, client_id, client_secret, scope
        self._post, self._clock = post, clock
        self._token = ""
        self._expires_at = 0.0

    def get(self) -> str:
        if not self._token or self._clock() >= self._expires_at - REFRESH_MARGIN_SECONDS:
            self._mint()
        return self._token

    def _mint(self) -> None:
        data = {"grant_type": "client_credentials"}
        if self._scope:
            data["scope"] = self._scope
        resp = self._post(self._url, data=data, auth=(self._id, self._secret), timeout=15)
        if resp.status_code in (400, 401, 403):
            raise TokenRefused(f"BURN: badge issuer refused the client (HTTP {resp.status_code})")
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = self._clock() + float(body.get("expires_in", 3600))


class BadgeAuth(httpx.Auth):
    """Adds `Authorization: Bearer <badge token>` to every gateway request."""

    def __init__(self, badge: BadgeToken):
        self._badge = badge

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self._badge.get()}"
        yield request
