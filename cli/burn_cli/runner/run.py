"""One running instance: start the session, fetch its own credentials, run the command, keep it alive."""
import json
import os
import signal
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable

from .. import retry
from ..api import Api, ApiError
from .badge import BadgeToken
from .credentials import CredentialSource
from .env import child_env
from .mcp_proxy import McpProxy
from . import privs
from .privs import Drop
from .spawn_endpoint import SpawnEndpoint

EXIT_STOPPED = 3  # the Burn API said the instance was burned or exited
STOP_GRACE = 5.0  # seconds between SIGTERM and SIGKILL when stopping the agent


def log(message: str) -> None:
    print(f"burn: {message}", file=sys.stderr, flush=True)


class Instance:
    def __init__(self, api: Api, source: CredentialSource, role: str, command: list[str], *,
                 gateway_url: str, host_env: dict | None = None, enroll_token: str = "", session: dict | None = None,
                 proxy_url: str = "", heartbeat_every: float = 15.0, min_heartbeat_gap: float = 3.0,
                 stop_grace: float = STOP_GRACE, drop: Drop | None = None, user_arg: str | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.api, self.source, self.role, self.command = api, source, role, command
        self.gateway_url, self.host_env = gateway_url, dict(os.environ if host_env is None else host_env)
        self.enroll_token, self.session, self.proxy_url = enroll_token, session, proxy_url
        self.heartbeat_every, self.min_gap, self.stop_grace, self._sleep = heartbeat_every, min_heartbeat_gap, stop_grace, sleep
        self._activity, self._done = threading.Event(), threading.Event()
        self._proc: subprocess.Popen | None = None
        self._children: list[subprocess.Popen] = []
        self._stopped_by_api = False
        self.drop, self.user_arg = drop, user_arg

    # -- session ------------------------------------------------------------------------------
    def start_session(self) -> dict:
        if self.session is None:
            body = {"role": self.role, "enroll_token": self.enroll_token}
            self.session = retry.call(lambda: self.api.request("POST", "/sessions", body), sleep=self._sleep)
        return self.session

    # -- child instances ----------------------------------------------------------------------
    def spawn_child(self, role: str, command: list[str]) -> dict:
        """Start a child instance: its session is made here, then it runs under its own `burn run`."""
        body = {"role": role, "parent_session": self.session["session_id"]}
        child = retry.call(lambda: self.api.request("POST", "/sessions", body), sleep=self._sleep)
        argv = [sys.executable, "-m", "burn_cli", "run", "--role", role, "--adopt", json.dumps(child),
                *(["--user", self.user_arg] if self.user_arg else []), "--", *command]
        # The child's supervisor gets this supervisor's host environment (API settings), never the agent's.
        proc = subprocess.Popen(argv, env=self.host_env)
        self._children.append(proc)
        return {"session_id": child["session_id"], "agent_id": child["agent_id"]}

    # -- lifecycle ----------------------------------------------------------------------------
    def run(self) -> int:
        session = self.start_session()
        creds = self.source.fetch(session)
        badge = BadgeToken(creds.badge)
        local_token = secrets.token_urlsafe(32)  # per run: only this instance's agent is told it
        mcp = McpProxy(self.gateway_url, badge, self._activity.set, local_token).start()
        spawn = SpawnEndpoint(self.spawn_child, local_token).start()
        home = self._make_home()
        env = child_env(self.host_env, session=session, credentials=creds, mcp_url=mcp.url,
                        spawn_url=spawn.url, proxy_base=self.proxy_url, home=home, local_token=local_token)
        log(f"instance {session['agent_id']} (session {session['session_id']}, role {session.get('role', self.role)}) started")
        try:
            self._proc = subprocess.Popen(self.command, env=env, start_new_session=True,
                                          **(self.drop.popen_args() if self.drop else {}))
        except OSError as exc:
            log(f"cannot start {self.command[0]!r}: {exc.strerror}")
            self._exit()
            mcp.stop(), spawn.stop()
            shutil.rmtree(home, ignore_errors=True)
            privs.release(self.drop)
            return 127
        beat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        beat.start()
        previous = self._forward_signals()
        try:
            code = self._proc.wait()
        finally:
            self._done.set()
            self._restore_signals(previous)
            mcp.stop(), spawn.stop()
            self._stop_children()
            if self.drop:
                privs.kill_all(self.drop.uid)  # nothing the agent started outlives the instance
            shutil.rmtree(home, ignore_errors=True)  # last, so a child cannot recreate it behind us
            privs.release(self.drop)
        if self._stopped_by_api:
            log("the instance was burned or has exited; the agent was stopped")
            return EXIT_STOPPED
        self._exit()
        return code if code >= 0 else 128 - code

    def _make_home(self) -> str:
        """A new private HOME for the agent, owned by the agent's user and removed when the instance ends."""
        home = tempfile.mkdtemp(prefix="burn-home-")  # mode 0700
        if self.drop:
            os.chown(home, self.drop.uid, self.drop.gid)
        return home

    def _exit(self) -> None:
        try:
            retry.call(lambda: self.api.request("POST", f"/instances/{self.session['session_id']}/exit"),
                       attempts=4, sleep=self._sleep)
        except ApiError as exc:
            log(f"could not report the exit ({exc})")

    def _heartbeat_loop(self) -> None:
        path = f"/instances/{self.session['session_id']}/heartbeat"
        last = 0.0
        while not self._done.is_set():
            woke = self._activity.wait(self.heartbeat_every)  # MCP activity wakes the loop early
            if self._done.is_set():
                return
            if woke:
                self._activity.clear()
                if time.monotonic() - last < self.min_gap:
                    continue
            last = time.monotonic()
            try:
                self.api.request("POST", path)
            except ApiError as exc:
                if exc.status in (404, 409):  # burned, exited or gone: the agent must not keep running
                    self._stopped_by_api = True
                    self._stop_agent()
                    return
                # anything else (network, 5xx, 429) is transient; the next beat tries again

    def _stop_agent(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        self._signal_group(signal.SIGTERM)
        try:
            proc.wait(self.stop_grace)
        except subprocess.TimeoutExpired:
            self._signal_group(signal.SIGKILL)

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self._proc.pid, sig)  # the agent runs in its own group, so its subprocesses go too
        except (ProcessLookupError, PermissionError):
            pass

    def _stop_children(self) -> None:
        for child in self._children:
            if child.poll() is None:
                child.terminate()
        for child in self._children:
            try:
                child.wait(self.stop_grace + 2)
            except subprocess.TimeoutExpired:
                child.kill()

    def _forward_signals(self) -> dict:
        if threading.current_thread() is not threading.main_thread():
            return {}
        previous = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda s, _f: self._signal_group(s))
        return previous

    def _restore_signals(self, previous: dict) -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
