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
from ..api import ApiError
from .badge import BadgeToken
from . import credentials as creds_mod
from .env import TASK_NAMES, child_env, task_values
from .instance_api import InstanceApi
from .mcp_proxy import McpProxy
from . import privs
from .privs import Drop
from .progress import ProgressQueue
from .spawn_endpoint import SpawnEndpoint

EXIT_STOPPED = 3  # the Burn API said the instance was burned or exited
ADOPT_SECRETS = "BURN_ADOPT_SECRETS"  # how a supervisor receives the secrets of a session someone else started
STOP_GRACE = 5.0  # seconds between SIGTERM and SIGKILL when stopping the agent


def log(message: str) -> None:
    print(f"burn: {message}", file=sys.stderr, flush=True)


class Instance:
    def __init__(self, api: InstanceApi, role: str, command: list[str], *,
                 gateway_url: str, host_env: dict | None = None, enroll_token: str = "", session: dict | None = None,
                 secrets: dict | None = None,
                 proxy_url: str = "", heartbeat_every: float = 15.0, min_heartbeat_gap: float = 3.0,
                 stop_grace: float = STOP_GRACE, drop: Drop | None = None, user_arg: str | None = None, stdin_devnull: bool = False,
                 progress_wait: float = 15.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.api, self.role, self.command = api, role, command
        self._secrets = secrets  # handed over by the parent supervisor or the launcher; never kept on self.session
        self._token = ""  # this instance's session token: supervisor memory only, never in the agent's env or argv
        self.gateway_url, self.host_env = gateway_url, dict(os.environ if host_env is None else host_env)
        admin = self.host_env.pop("BURN_DEMO_TOKEN", None)  # an instance never uses the admin token, nor passes it on
        # Raises BadTask before anything starts. No item may be a credential this supervisor holds.
        self._task = task_values(self.host_env, secrets=(admin, enroll_token, self.host_env.get("BURN_JOIN_TOKEN")))
        for name in TASK_NAMES:  # the task is this instance's: a child it spawns gets none
            self.host_env.pop(name, None)
        self.enroll_token, self.session, self.proxy_url = enroll_token, session, proxy_url
        self.heartbeat_every, self.min_gap, self.stop_grace, self._sleep = heartbeat_every, min_heartbeat_gap, stop_grace, sleep
        self._activity, self._done = threading.Event(), threading.Event()
        self._proc: subprocess.Popen | None = None
        self._children: list[subprocess.Popen] = []
        self._stopped_by_api = False
        self.drop, self.user_arg, self.stdin_devnull = drop, user_arg, stdin_devnull
        self._stopping, self._spawn_lock = threading.Event(), threading.Lock()
        self._progress = ProgressQueue(on_change=self._activity.set)  # new progress wakes the heartbeat
        self.progress_wait = progress_wait  # how long the agent's "started" may wait for Burn to record it
        self._beat_lock = threading.Lock()  # one heartbeat at a time, so a batch is never sent twice at once
        self._bare_next = False  # after a refused batch: the API recorded nothing, not even that we are alive

    # -- session ------------------------------------------------------------------------------
    def start_session(self) -> dict:
        if self.session is None:
            self.session = retry.call(lambda: self.api.join(self.role, self.enroll_token), sleep=self._sleep)
        return self.session

    # -- child instances ----------------------------------------------------------------------
    def spawn_child(self, role: str, command: list[str]) -> dict:
        """Start a child instance: its session is made here, then it runs under its own `burn run`."""
        if self._stopping.is_set():
            raise ApiError("this instance is stopping", 409)
        child = retry.call(lambda: self.api.spawn(role, self.session["session_id"], self._token), sleep=self._sleep)
        child_secrets = child.pop("secrets", None)
        child_token = (child_secrets or {}).get("session_token", "")
        if not child_secrets or not child_token:
            if child_token:
                self._report_exit(child["session_id"], child_token)
            raise ApiError(str(creds_mod.MissingSecrets("session token" if child_secrets else "secrets")), 502)
        # argv is readable by every uid in the container (/proc/<pid>/cmdline): only non-secret session fields go
        # there. The child's own values go in its exec environment, which only its uid and root can read.
        argv = [sys.executable, "-m", "burn_cli", "run", "--role", role, "--adopt", json.dumps(child),
                *(["--user", self.user_arg] if self.user_arg else []), "--", *command]
        with self._spawn_lock:  # _stop_children takes the same lock, so no child starts after it ran
            if self._stopping.is_set():
                self._report_exit(child["session_id"], child_token)
                raise ApiError("this instance is stopping", 409)
            # The child's supervisor gets this supervisor's host environment (API settings), never the agent's.
            env = {**self.host_env, ADOPT_SECRETS: json.dumps(child_secrets)}
            self._children.append(subprocess.Popen(argv, env=env))
        return {"session_id": child["session_id"], "agent_id": child["agent_id"]}

    # -- lifecycle ----------------------------------------------------------------------------
    def run(self) -> int:
        original = self._guard_setup()
        try:
            return self._run_guarded()
        finally:
            self._restore_signals(original)

    def _guard_setup(self) -> dict:
        """Until the agent runs and signals are forwarded to it, SIGTERM and SIGHUP end this instance cleanly
        (the exit is reported) instead of killing the supervisor with its session still active."""
        if threading.current_thread() is not threading.main_thread():
            return {}

        def stop(sig, _frame):
            raise SystemExit(128 + sig)

        return {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGHUP)}

    def _run_guarded(self) -> int:
        session = self.start_session()
        own = self._secrets or session.pop("secrets", None)  # arrive once; nothing keeps a copy on the session
        self._secrets, self._token = None, (own or {}).get("session_token", "")
        cleanups: list[Callable[[], None]] = []
        try:
            return self._run(session, own, cleanups)
        except BaseException:
            self._exit()  # the session started, so end it even if setup failed (credentials, HOME, ...)
            raise
        finally:
            self._done.set()
            self._stopping.set()
            for cleanup in reversed(cleanups):
                cleanup()

    def _run(self, session: dict, own: dict | None, cleanups: list) -> int:
        creds = creds_mod.from_secrets(own)  # the agent's values; the session token is not among them
        creds_mod.session_token_of(own)  # an API that sends no session token cannot authenticate heartbeats
        badge = BadgeToken(creds.badge)
        local_token = secrets.token_urlsafe(32)  # per run: only this instance's agent is told it
        mcp = McpProxy(self.gateway_url, badge, self._activity.set, local_token).start()
        cleanups.append(mcp.stop)
        spawn = SpawnEndpoint(self.spawn_child, local_token, progress=self._progress,
                              progress_wait=self.progress_wait).start()
        cleanups.append(spawn.stop)
        home = self._make_home()
        cleanups.append(lambda: shutil.rmtree(home, ignore_errors=True))  # last, so nothing recreates it behind us
        env = child_env(self.host_env, session=session, credentials=creds, mcp_url=mcp.url,
                        spawn_url=spawn.url, proxy_base=self.proxy_url, home=home, local_token=local_token,
                        progress_url=spawn.progress_url, task=self._task)
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
        self._done.set()
        self._flush_progress()  # what the agent reported just before it exited still counts
        self._exit()
        return code if code >= 0 else 128 - code

    def _make_home(self) -> str:
        """A new private HOME for the agent, owned by the agent's user and removed when the instance ends."""
        home = tempfile.mkdtemp(prefix="burn-home-")  # mode 0700
        if self.drop:
            os.chown(home, self.drop.uid, self.drop.gid)
        return home

    def _exit(self) -> None:
        if not self._token:
            log("no session token, so the exit cannot be reported; the instance will time out")
            return
        self._report_exit(self.session["session_id"], self._token)

    def _report_exit(self, session_id: str, session_token: str) -> None:
        try:
            retry.call(lambda: self.api.exit(session_id, session_token), attempts=4, sleep=self._sleep)
        except ApiError as exc:
            log(f"could not report the exit ({exc})")

    def _heartbeat_loop(self) -> None:
        last = 0.0
        while not self._done.is_set():
            woke = self._activity.wait(self.heartbeat_every)  # MCP activity and new progress wake the loop early
            if self._done.is_set():
                return
            if woke:
                self._activity.clear()
                gap_left = self.min_gap - (time.monotonic() - last)
                if gap_left > 0:
                    if not (self._progress.pending() or self._bare_next):
                        continue  # MCP activity just after a beat: that beat already showed the instance alive
                    if self._done.wait(gap_left):  # progress goes out as soon as the minimum gap allows
                        return
            last = time.monotonic()
            status = self._beat()
            if status in (404, 409):  # burned, exited or gone: the agent must not keep running
                self._stopped_by_api = True
                self._progress.gone()  # a start the agent waits on is answered 409: it must not act on it
                self._stop_agent()
                return
            retry_soon = status not in (200, 400) and self._progress.waiting()  # a start is blocked on this
            if self._bare_next or (status == 200 and self._progress.pending()) or retry_soon:
                self._activity.set()  # the next beat goes after the minimum gap: a bare one, the next 50, or a retry
            # anything else (network, 5xx, 429) is transient; the next beat tries again, with the same progress

    def _beat(self) -> int:
        """One heartbeat with the oldest queued progress, at most 50 events, or none right after a refused batch.

        Returns 200, or the failed call's status (0 when the API was unreachable).
        """
        with self._beat_lock:
            bare, self._bare_next = self._bare_next, False  # one bare try per refused batch, whatever its outcome
            batch = [] if bare else self._progress.batch()
            try:
                self.api.heartbeat(self.session["session_id"], self._token, batch)
            except ApiError as exc:
                if exc.status == 400 and batch:  # a broken progress rule, never a burn (that is a 409)
                    self._progress.dropped(len(batch), str(exc))
                    self._bare_next = True  # a 400 records nothing, so the next beat proves the instance alive
                    log(f"the Burn API refused {len(batch)} progress events, which are dropped ({exc})")
                return exc.status or 0
            self._progress.sent(len(batch))
            return 200

    def _flush_progress(self) -> None:
        """Send the progress still queued when the agent exits, so the instance's record is complete.

        A transient failure (network, 5xx, a 429 when progress changed while the API saved it) is tried again,
        up to three times in a row.
        """
        failures = 0
        while self._progress.pending():
            status = self._beat()
            failures = 0 if status in (200, 400) else failures + 1
            if status in (404, 409) or failures == 3:
                log(f"{self._progress.pending()} progress events could not be sent before the exit")
                return
            if failures:
                self._sleep(retry.delay(failures))

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
