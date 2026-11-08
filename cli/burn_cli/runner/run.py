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
                 stop_grace: float = STOP_GRACE, drop: Drop | None = None, user_arg: str | None = None, stdin_devnull: bool = False,
                 sleep: Callable[[float], None] = time.sleep):
        self.api, self.source, self.role, self.command = api, source, role, command
        self.gateway_url, self.host_env = gateway_url, dict(os.environ if host_env is None else host_env)
        self.enroll_token, self.session, self.proxy_url = enroll_token, session, proxy_url
        self.heartbeat_every, self.min_gap, self.stop_grace, self._sleep = heartbeat_every, min_heartbeat_gap, stop_grace, sleep
        self._activity, self._done = threading.Event(), threading.Event()
        self._proc: subprocess.Popen | None = None
        self._children: list[subprocess.Popen] = []
        self._stopped_by_api = False
        self.drop, self.user_arg, self.stdin_devnull = drop, user_arg, stdin_devnull
        self._stopping, self._spawn_lock = threading.Event(), threading.Lock()

    # -- session ------------------------------------------------------------------------------
    def start_session(self) -> dict:
        if self.session is None:
            body = {"role": self.role, "enroll_token": self.enroll_token}
            self.session = retry.call(lambda: self.api.request("POST", "/sessions", body), sleep=self._sleep)
        return self.session

    # -- child instances ----------------------------------------------------------------------
    def spawn_child(self, role: str, command: list[str]) -> dict:
        """Start a child instance: its session is made here, then it runs under its own `burn run`."""
        if self._stopping.is_set():
            raise ApiError("this instance is stopping", 409)
        body = {"role": role, "parent_session": self.session["session_id"]}
        child = retry.call(lambda: self.api.request("POST", "/sessions", body), sleep=self._sleep)
        # argv is readable by every uid in the container (/proc/<pid>/cmdline): pass only non-secret session fields
        public = {k: v for k, v in child.items() if k != "secrets"}
        argv = [sys.executable, "-m", "burn_cli", "run", "--role", role, "--adopt", json.dumps(public),
                *(["--user", self.user_arg] if self.user_arg else []), "--", *command]
        with self._spawn_lock:  # _stop_children takes the same lock, so no child starts after it ran
            if self._stopping.is_set():
                self._report_exit(child["session_id"])
                raise ApiError("this instance is stopping", 409)
            # The child's supervisor gets this supervisor's host environment (API settings), never the agent's.
            self._children.append(subprocess.Popen(argv, env=self.host_env))
        return {"session_id": child["session_id"], "agent_id": child["agent_id"]}

    # -- lifecycle ----------------------------------------------------------------------------
    def run(self) -> int:
        session = self.start_session()
        cleanups: list[Callable[[], None]] = []
        try:
            return self._run(session, cleanups)
        except BaseException:
            self._exit()  # the session started, so end it even if setup failed (credentials, HOME, ...)
            raise
        finally:
            self._done.set()
            self._stopping.set()
            for cleanup in reversed(cleanups):
                cleanup()

    def _run(self, session: dict, cleanups: list) -> int:
        creds = self.source.fetch(session)
        badge = BadgeToken(creds.badge)
        local_token = secrets.token_urlsafe(32)  # per run: only this instance's agent is told it
        mcp = McpProxy(self.gateway_url, badge, self._activity.set, local_token).start()
        cleanups.append(mcp.stop)
        spawn = SpawnEndpoint(self.spawn_child, local_token).start()
        cleanups.append(spawn.stop)
        home = self._make_home()
        cleanups.append(lambda: shutil.rmtree(home, ignore_errors=True))  # last, so nothing recreates it behind us
        env = child_env(self.host_env, session=session, credentials=creds, mcp_url=mcp.url,
                        spawn_url=spawn.url, proxy_base=self.proxy_url, home=home, local_token=local_token)
        log(f"instance {session['agent_id']} (session {session['session_id']}, role {session.get('role', self.role)}) started")
        try:
            self._proc = subprocess.Popen(self.command, env=env, start_new_session=True,
                                          # the join token may still have uses left: do not let the agent re-read it
                                          **({"stdin": subprocess.DEVNULL} if self.stdin_devnull else {}),
                                          **(self.drop.popen_args() if self.drop else {}))
        except OSError as exc:
            log(f"cannot start {self.command[0]!r}: {exc.strerror}")
            self._exit()
            return 127
        cleanups.append(self._stop_children)
        cleanups.append(self._kill_everything)  # nothing the agent started outlives the instance
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        previous = self._forward_signals()
        cleanups.append(lambda: self._restore_signals(previous))
        code = self._proc.wait()
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
        self._report_exit(self.session["session_id"])

    def _report_exit(self, session_id: str) -> None:
        try:
            retry.call(lambda: self.api.request("POST", f"/instances/{session_id}/exit"),
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
        """SIGTERM the agent's whole group, then SIGKILL whatever is left after the grace period."""
        self._stopping.set()
        if self._proc is None:
            return
        self._signal_group(signal.SIGTERM)
        self._escalate()

    def _escalate(self) -> None:
        """Wait up to the grace period for the group to empty, then kill it, leader exited or not."""
        deadline = time.monotonic() + self.stop_grace
        while time.monotonic() < deadline and self._group_alive():
            time.sleep(0.05)
        self._kill_everything()

    def _group_alive(self) -> bool:
        try:
            os.killpg(self._proc.pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _kill_everything(self) -> None:
        if self._proc is not None:
            self._signal_group(signal.SIGKILL)  # members that ignored SIGTERM survive their leader otherwise
        if self.drop and self.drop.claim:  # only a uid of the instance's own: --user's is shared with other instances
            privs.kill_all(self.drop.uid)

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(self._proc.pid, sig)  # the agent runs in its own group, so its subprocesses go too
        except (ProcessLookupError, PermissionError):
            pass

    def _on_signal(self, sig: int, _frame=None) -> None:
        """SIGINT is passed on as is; SIGTERM and SIGHUP both mean terminate. Then escalate, so a forwarded
        signal that the agent ignores cannot leave it running when this supervisor is itself killed."""
        self._stopping.set()
        self._signal_group(signal.SIGINT if sig == signal.SIGINT else signal.SIGTERM)
        threading.Thread(target=self._escalate, daemon=True).start()

    def _stop_children(self) -> None:
        with self._spawn_lock:
            self._stopping.set()
            children = list(self._children)
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(self.stop_grace + 2)  # a child supervisor escalates on its own within the grace period
            except subprocess.TimeoutExpired:
                child.kill()

    def _forward_signals(self) -> dict:
        if threading.current_thread() is not threading.main_thread():
            return {}
        previous = {}
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, self._on_signal)
        return previous

    def _restore_signals(self, previous: dict) -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
