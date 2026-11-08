"""The launcher's side of replace on burn, against a fake Burn API and a fake docker."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_swarm  # noqa: E402
from swarm_launch import start_workers  # noqa: E402


class FakeBurn:
    """The person-side client and the instance client in one, recording every call.

    `reads` maps a session id to the replies GET /instances/{sid} gives, one per read (the last one repeats).
    """

    def __init__(self):
        self.calls, self.n, self.tokens = [], 0, 0
        self.reads: dict[str, list[dict]] = {}
        self.join_error = self.token_error = self.replace_error = None
        self.linked = {"done": ["w01-item-01", "w01-item-02"], "quarantined": ["w01-item-03"]}

    def request(self, method, path, body=None):  # person-side: the launcher's own sign-in
        self.calls.append((method, path, body))
        if path.endswith("/tokens"):
            if self.token_error:
                raise self.token_error
            self.tokens += 1
            return {"token": f"bjt_{self.tokens:016x}.secret{self.tokens}"}
        if method == "GET":
            replies = self.reads[path.rsplit("/", 1)[-1]]
            return replies.pop(0) if len(replies) > 1 else replies[0]
        if path.endswith("/replace"):
            if self.replace_error:
                raise self.replace_error
            return {"replaces": path.split("/")[2], "session_id": body["session_id"], "generation": 1,
                    "cutoff": "2026-11-07T22:00:00Z", "cutoff_source": "burn_time", **self.linked}
        raise AssertionError(f"unexpected call {method} {path}")

    def join(self, role, enroll_token):  # the instance client: no person credentials
        self.calls.append(("join", role, enroll_token))
        if self.join_error:
            raise self.join_error
        self.n += 1
        return {"session_id": f"sess_{self.n}", "agent_id": f"agent-worker-{self.n:06x}", "role": role,
                "tools": ["read_inbox"],
                "secrets": {"badge": {"client_id": f"cid{self.n}", "client_secret": f"badge-secret-{self.n}",
                                      "token_url": "http://localhost:8700/mock-token", "scope": "s"},
                            "session_token": "bst_" + f"{self.n}" * 43}}

    def exit(self, session_id, session_token):
        self.calls.append(("exit", session_id, session_token))
        return {}

    def made(self, kind):
        return [c for c in self.calls if c[0] == kind or (kind == "tokens" and c[1].endswith("/tokens"))]


class Docker:
    def __init__(self):
        self.runs = []

    def __call__(self, argv, env):
        self.runs.append((argv, env))


def swarm_of(count=1, items=4, burn=None, docker=None):
    burn, docker, logged, slept = burn or FakeBurn(), docker or Docker(), [], []
    swarm = start_workers(burn, burn, "worker", count, api_url="http://localhost:8700",
                          gateway_url="http://localhost:8701/mcp", mock=True, interval=3.0, certs_dir="/c",
                          docker=docker, items=items, sleep=slept.append)
    return swarm, burn, docker, logged, slept


# --- giving workers a task ----------------------------------------------------------------------------------------

def test_each_worker_gets_its_own_task_and_the_launcher_keeps_the_map():
    swarm, burn, docker, _, _ = swarm_of(count=2, items=3)
    assert burn.calls[0] == ("POST", "/roles/worker/tokens", {"uses": 2, "ttl": 3600})  # no spare uses
    tasks = [json.loads(env["BURN_TASK"]) for _, env in docker.runs]
    assert tasks == [["w01-item-01", "w01-item-02", "w01-item-03"], ["w02-item-01", "w02-item-02", "w02-item-03"]]
    assert all("BURN_RESUME" not in env for _, env in docker.runs)  # a first instance resumes nothing
    assert [w.task for w in swarm.workers.values()] == tasks and list(swarm.workers) == swarm.names
    for argv, env in docker.runs:  # passed by name like every other value, never on the command line
        assert "BURN_TASK" in argv and "w01-item-01" not in " ".join(argv)


def test_without_items_nothing_changes():
    swarm, burn, docker, _, _ = swarm_of(count=2, items=0)
    assert burn.calls[0] == ("POST", "/roles/worker/tokens", {"uses": 2, "ttl": 3600})
    assert all("BURN_TASK" not in env for _, env in docker.runs)


# --- the command line -----------------------------------------------------------------------------------------

def run_main(monkeypatch, *args):
    captured = {}

    class Started:
        names: list = []

    monkeypatch.setattr(sys, "argv", ["run_swarm.py", "--role", "worker", "--count", "2", "--mock", *args])
    monkeypatch.setattr(run_swarm, "docker", lambda *a, **k: None)
    monkeypatch.setattr(run_swarm, "start_workers", lambda api, instances, role, count, **kw: captured.update(kw) or Started())
    run_swarm.main()
    return captured


def test_items_are_passed_to_the_launcher_and_default_to_none(monkeypatch):
    assert run_main(monkeypatch)["items"] == 0
    assert run_main(monkeypatch, "--items", "10")["items"] == 10


@pytest.mark.parametrize("items", ["-1", "501"])
def test_a_task_size_burn_could_not_track_is_refused(monkeypatch, items):
    with pytest.raises(SystemExit, match="--items"):
        run_main(monkeypatch, "--items", items)
