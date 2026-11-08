"""burn: the small SDK for code running as a Burn instance.

    import burn

    with burn.session("worker") as me:        # joins with BURN_JOIN_TOKEN
        print(me.agent_id)

    burn.spawn("worker", ["python", "task.py"])   # inside `burn run`: starts a child instance
"""
import json
import os
import shlex
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass

from burn_cli import config, retry
from burn_cli.api import ApiError
from burn_cli.runner.instance_api import InstanceApi

__all__ = ["Session", "session", "spawn", "BurnError"]


class BurnError(Exception):
    pass


@dataclass(frozen=True)
class Session:
    session_id: str
    agent_id: str
    role: str
    badge_ref: str
    credentials: list


def _api() -> InstanceApi:
    url = config.get("BURN_API_URL")
    if not url:
        raise BurnError("BURN_API_URL not set. Export it or run `make env` to write .env.")
    return InstanceApi(url)  # an instance authenticates as itself: no admin or person token


@contextmanager
def session(role: str, token: str | None = None):
    """Start a session for `role` and end it cleanly on exit.

    The join token is `token`, else BURN_JOIN_TOKEN. Rate limits and server errors are retried with backoff.
    """
    token = token or os.environ.get("BURN_JOIN_TOKEN", "")
    if not token:
        raise BurnError("a join token is required: pass token= or set BURN_JOIN_TOKEN")
    api = _api()
    try:
        r = retry.call(lambda: api.join(role, token))
    except ApiError as exc:
        raise BurnError(str(exc)) from None
    session_token = (r.get("secrets") or {}).get("session_token", "")  # kept here only, to end this session
    current = Session(r["session_id"], r["agent_id"], r.get("role", role), r["badge_ref"], r.get("credentials", []))
    try:
        yield current
    finally:
        try:
            api.exit(current.session_id, session_token)
        except ApiError:
            pass  # the instance may already be burned; the API times it out otherwise


def spawn(role: str, command: list[str] | str) -> dict:
    """Ask the surrounding `burn run` to start `command` as a child instance of `role`.

    Returns {"session_id", "agent_id"}. Only works inside `burn run` (BURN_SPAWN_URL is set there).
    """
    url = os.environ.get("BURN_SPAWN_URL")
    if not url:
        raise BurnError("BURN_SPAWN_URL is not set: spawn() only works inside `burn run`")
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    req = urllib.request.Request(url, method="POST", data=json.dumps({"role": role, "command": argv}).encode(),
                                 headers={"content-type": "application/json",
                                          "authorization": f"Bearer {os.environ.get('BURN_LOCAL_TOKEN', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        try:
            detail = json.loads(err.read()).get("error", "")
        except ValueError:
            detail = ""
        raise BurnError(f"spawn {role} failed with HTTP {err.code}" + (f": {detail}" if detail else "")) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BurnError(f"cannot reach the spawn endpoint ({getattr(exc, 'reason', exc)})") from None
