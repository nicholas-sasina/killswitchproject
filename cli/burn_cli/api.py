"""Tiny Burn API client (stdlib only)."""
import json
import urllib.error
import urllib.request


class ApiError(Exception):
    pass


class Api:
    def __init__(self, base_url: str, token: str):
        self.base_url, self._token = base_url, token  # token is only ever sent as a header

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base_url + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"x-burn-demo-token": self._token, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            try:
                detail = json.loads(err.read()).get("error", "")
            except ValueError:
                detail = ""
            raise ApiError(f"{method} {path} failed with HTTP {err.code}" + (f": {detail}" if detail else "")) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ApiError(f"cannot reach the Burn API ({getattr(exc, 'reason', exc)})") from None
