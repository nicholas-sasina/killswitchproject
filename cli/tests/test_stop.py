"""Stopping an instance leaves nothing running, and setup failures still end the session."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from burn_cli.api import ApiError
from test_run import EXIT_STOPPED, API_TOKEN, host_env, instance, py, stack  # noqa: F401  (fixture)


def gone(pid: int, wait: float = 5.0) -> bool:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def wait_for(path, wait=10.0):
    deadline = time.monotonic() + wait
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return path.exists()


STUBBORN_GRANDCHILD = """
    import os, signal, subprocess, sys, time
    # the leader hands the group a member that ignores SIGTERM, then exits on SIGTERM itself
    member = subprocess.Popen([sys.executable, "-c", "import signal, time, os; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                               "open(%r, 'w').write(str(os.getpid())); time.sleep(60)"])
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    time.sleep(60)
"""


def test_a_member_that_ignores_sigterm_does_not_outlive_its_leader(stack):
    stack.heartbeat_status = 409
    pidfile = stack.tmp / "member.pid"
    code = STUBBORN_GRANDCHILD.replace("%r", repr(str(pidfile)))
    started = time.monotonic()
    assert instance(stack, py(code), heartbeat_every=0.5, stop_grace=1.0).run() == EXIT_STOPPED
    assert wait_for(pidfile) and gone(int(pidfile.read_text())), "the stubborn member survived the stop"
    assert time.monotonic() - started < 15


def run_burn(stack, agent_code, *extra):
    env = {**host_env(stack), "BURN_DEMO_TOKEN": API_TOKEN, "BURN_JOIN_TOKEN": stack.token}
    return subprocess.Popen([sys.executable, "-m", "burn_cli", "run", "--role", "worker", *extra, "--",
                             sys.executable, "-c", agent_code], env=env)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_a_forwarded_signal_escalates_to_sigkill_for_an_agent_that_ignores_it(stack, sig):
    pidfile = stack.tmp / f"agent-{sig}.pid"
    code = f"""
import os, signal, time
for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(s, signal.SIG_IGN)
open({str(pidfile)!r}, "w").write(str(os.getpid()))
time.sleep(60)"""
    burn = run_burn(stack, code)
    assert wait_for(pidfile)
    agent = int(pidfile.read_text())
    burn.send_signal(sig)
    assert gone(agent, 20), "the agent outlived the signal sent to its supervisor"
    burn.wait(20)


def test_sighup_is_handled_like_sigterm(stack):
    marker = stack.tmp / "got"
    code = f"""
import pathlib, signal, sys, time
signal.signal(signal.SIGTERM, lambda *a: (pathlib.Path({str(marker)!r}).write_text("term"), sys.exit(0)))
pathlib.Path({str(marker) + ".ready"!r}).write_text("1")
time.sleep(60)"""
    burn = run_burn(stack, code)
    assert wait_for(stack.tmp / "got.ready")
    burn.send_signal(signal.SIGHUP)
    assert burn.wait(20) == 0 and marker.read_text() == "term"


def test_no_spawn_once_the_instance_is_stopping(stack):
    inst = instance(stack, py("pass"))
    inst.session = {"session_id": "sess_p", "agent_id": "agent-worker-aaaaaa"}
    inst._stopping.set()
    with pytest.raises(ApiError) as exc:
        inst.spawn_child("worker", ["true"])
    assert exc.value.status == 409 and not stack.calls("POST", "/sessions")


def test_a_spawn_that_loses_the_race_with_shutdown_ends_its_session(stack):
    stack.roles["worker"]["may_spawn"] = ["worker"]
    parent = stack.add_instance("sess_p", "worker")
    inst = instance(stack, py("pass"))
    inst.session = {"session_id": "sess_p", "agent_id": parent["agent_id"]}
    original = stack.handle

    def stop_while_the_session_is_being_made(method, path, query, body):
        out = original(method, path, query, body)
        if (method, path) == ("POST", "/sessions"):
            inst._stopping.set()
        return out

    stack.handle = stop_while_the_session_is_being_made
    with pytest.raises(ApiError):
        inst.spawn_child("worker", ["true"])
    child = next(i for i in stack.instances.values() if i["parent_session"] == "sess_p")
    assert child["status"] == "exited" and inst._children == []


def test_a_failure_during_setup_still_ends_the_session(stack):
    class Boom:
        def fetch(self, session):
            raise RuntimeError("secrets are unreachable")

    inst = instance(stack, py("pass"))
    inst.source = Boom()
    with pytest.raises(RuntimeError):
        inst.run()
    (row,) = stack.instances.values()
    assert row["status"] == "exited"


def test_home_failure_during_setup_still_ends_the_session(stack, monkeypatch):
    inst = instance(stack, py("pass"))
    monkeypatch.setattr(inst, "_make_home", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        inst.run()
    assert next(iter(stack.instances.values()))["status"] == "exited"


def test_the_agent_cannot_reread_a_join_token_from_stdin(stack, capfd):
    code = """
        import os
        print("devnull" if os.path.samestat(os.fstat(0), os.stat("/dev/null")) else "inherited")"""
    assert instance(stack, py(code), stdin_devnull=True).run() == 0
    assert capfd.readouterr().out.strip() == "devnull"


def test_spawn_never_puts_secrets_in_the_child_supervisors_argv(stack, monkeypatch):
    import burn_cli.runner.run as runmod
    stack.roles["worker"]["may_spawn"] = ["worker"]
    parent = stack.add_instance("sess_p", "worker")
    secrets_field = {"badge": {"client_id": "cid-xyz", "client_secret": "client-secret-xyz", "token_url": "https://t/xyz"},
                     "api_key": "mpk_live_secretvalue", "aws_key": {"access_key_id": "AKIASECRETXYZ",
                                                                  "secret_access_key": "aws-secret-xyz"},
                     "session_token": "bst_secrettoken"}
    original = stack.handle

    def with_secrets(method, path, query, body):
        status, out = original(method, path, query, body)
        if (method, path) == ("POST", "/sessions") and status == 201:
            out = {**out, "secrets": secrets_field}
        return status, out

    stack.handle = with_secrets
    seen = []

    class Recorder:
        def __init__(self, argv, **kw):
            seen.append(list(argv))

        def poll(self):
            return 0

    monkeypatch.setattr(runmod.subprocess, "Popen", Recorder)
    inst = instance(stack, py("pass"))
    inst.session = {"session_id": "sess_p", "agent_id": parent["agent_id"]}
    result = inst.spawn_child("worker", ["true"])
    (argv,) = seen
    joined = " ".join(argv)
    assert '"secrets"' not in joined and "secrets" not in json.loads(argv[argv.index("--adopt") + 1])
    for value in ("client-secret-xyz", "mpk_live_secretvalue", "AKIASECRETXYZ", "aws-secret-xyz", "bst_secrettoken"):
        assert value not in joined
    assert json.loads(argv[argv.index("--adopt") + 1])["session_id"] == result["session_id"]  # the rest still goes


def test_sigterm_during_setup_still_reports_the_exit(stack, monkeypatch):
    """docker stop landing after the session started but before the agent runs must not leave it active."""
    inst = instance(stack, py("pass"))
    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(inst, "_make_home", lambda: os.kill(os.getpid(), signal.SIGTERM) or "/nonexistent")
    with pytest.raises(SystemExit) as exc:
        inst.run()
    assert exc.value.code == 128 + signal.SIGTERM
    (row,) = stack.instances.values()
    assert row["status"] == "exited"
    assert signal.getsignal(signal.SIGTERM) is before  # the handler is put back


def test_the_agent_dying_nonzero_still_reports_the_exit(stack):
    assert instance(stack, py("import sys; sys.exit(1)")).run() == 1
    assert next(iter(stack.instances.values()))["status"] == "exited"
