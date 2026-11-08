"""Tiny Burn API client (stdlib only)."""
import json
import urllib.error
import urllib.request

from burn_contracts import names

from . import signin


class ApiError(Exception):
    """An API call failed. `status` is the HTTP status, or None when the API was unreachable."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        """Rate limits, server errors and network failures can succeed on a later attempt."""
        return self.status is None or self.status == 429 or self.status >= 500


_UNAUTHORIZED_HINT = {
    True: " (run `burn login` to sign in again)",
    False: " (if this stack now requires sign-in, run `make env`, then `burn login`)",
}


class Api:
    """The person-side client: what commands run by a signed-in person use. (A running instance authenticates as
    itself and has its own client, runner/instance_api.py.)"""

    def __init__(self, base_url: str, token: str = ""):
        # The demo token, given (by config.load) only while the stack does not require sign-in, or in local
        # development. Without one, requests carry the signed-in person's ID token.
        self.base_url, self._token = base_url, token

    def auth_headers(self) -> dict:
        """The one place a person's credentials become a request header.

        `Authorization: Bearer <ID token>` from `burn login`, refreshed when it is about to expire; the demo token
        instead when one was given. A subclass may authenticate differently (the swarm launcher signs in as the
        automation person).
        """
        if self._token:
            return {names.DEMO_TOKEN_HEADER: self._token}
        try:
            return {"authorization": f"Bearer {signin.current_id_token()}"}
        except signin.SignInError as exc:
            raise ApiError(str(exc), 401) from None

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        auth = self.auth_headers()
        req = urllib.request.Request(
            self.base_url + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={**auth, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            try:
                detail = json.loads(err.read()).get("error", "")
            except ValueError:
                detail = ""
            message = f"{method} {path} failed with HTTP {err.code}" + (f": {detail}" if detail else "")
            if err.code == 401:
                message += _UNAUTHORIZED_HINT["authorization" in auth]
            raise ApiError(message, err.code) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ApiError(f"cannot reach the Burn API ({getattr(exc, 'reason', exc)})") from None
