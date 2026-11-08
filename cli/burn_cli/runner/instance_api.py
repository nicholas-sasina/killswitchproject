"""The calls a running instance makes about itself: join, spawn, heartbeat, exit.

These carry their own authority, never the admin or person credentials: a join is authorized by the join token in
its body (no auth header at all), and everything after that by the instance's own session token as a bearer. This
is deliberately separate from `burn_cli.api.Api`, which is for people. The session token lives only in the
supervisor: it is passed per call and never put in the agent's environment, argv or logs.
"""
import json
import urllib.error
import urllib.request

from ..api import ApiError


class InstanceApi:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None, session_token: str = "") -> dict:
        headers = {"content-type": "application/json"}
        if session_token:
            headers["authorization"] = f"Bearer {session_token}"
        req = urllib.request.Request(self.base_url + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            try:
                detail = json.loads(err.read()).get("error", "")
            except ValueError:
                detail = ""
            raise ApiError(f"{method} {path} failed with HTTP {err.code}" + (f": {detail}" if detail else ""),
                           err.code) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise ApiError(f"cannot reach the Burn API ({getattr(exc, 'reason', exc)})") from None

    def join(self, role: str, enroll_token: str) -> dict:
        """Start an instance of a role. No auth header: the join token is the authority."""
        return self._request("POST", "/instances", {"role": role, "enroll_token": enroll_token})

    def spawn(self, role: str, parent_session: str, session_token: str) -> dict:
        """Start a child of this instance, with exactly {role, parent_session} and the parent's session token."""
        return self._request("POST", "/instances", {"role": role, "parent_session": parent_session}, session_token)

    def heartbeat(self, session_id: str, session_token: str) -> dict:
        return self._request("POST", f"/instances/{session_id}/heartbeat", session_token=session_token)

    def exit(self, session_id: str, session_token: str) -> dict:
        return self._request("POST", f"/instances/{session_id}/exit", session_token=session_token)
