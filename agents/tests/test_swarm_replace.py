"""The launcher's side of replace on burn, against a fake Burn API and a fake docker."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_swarm  # noqa: E402
from burn_cli.api import ApiError  # noqa: E402
from swarm_launch import ADOPT_SECRETS, start_workers  # noqa: E402

OLD_TASK = ["w01-item-01", "w01-item-02", "w01-item-03", "w01-item-04"]


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
                          docker=docker, items=items, sleep=slept.append, log=logged.append)
    return swarm, burn, docker, logged, slept


def burned(status="pending", **fields):
    return {"session_id": "sess_1", "status": "revoked", "generation": 0, "replaces": None, "replaced_by": None,
            "replacement": {"status": status, "cutoff": "2026-11-07T22:00:00Z", "done": [], "quarantined": []},
            **fields}


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
    swarm, burn, docker, logged, _ = swarm_of(count=2, items=0)
    assert burn.calls[0] == ("POST", "/roles/worker/tokens", {"uses": 2, "ttl": 3600})
    assert all("BURN_TASK" not in env for _, env in docker.runs)
    calls = len(burn.calls)
    assert swarm.on_exit(swarm.names[0]) is None
    assert len(burn.calls) == calls and len(docker.runs) == 2 and logged == []  # not even a read


# --- when a worker exits ------------------------------------------------------------------------------------------

def test_a_burned_worker_with_a_pending_replacement_is_replaced_once_with_the_same_task():
    swarm, burn, docker, logged, _ = swarm_of(count=1, items=4)
    old = swarm.names[0]
    burn.reads["sess_1"] = [burned()]
    new = swarm.on_exit(old)
    assert burn.calls[2:] == [  # after the first token and join
        ("GET", "/instances/sess_1", None),
        ("POST", "/roles/worker/tokens", {"uses": 1, "ttl": 300}),  # a fresh single-use token for this one join
        ("join", "worker", "bjt_0000000000000002.secret2"),
        ("POST", "/instances/sess_1/replace", {"session_id": "sess_2"}),
    ]
    argv, env = docker.runs[-1]
    assert argv[argv.index("--name") + 1] == new and new != old and len(docker.runs) == 2
    assert json.loads(env["BURN_TASK"]) == OLD_TASK
    assert json.loads(env["BURN_RESUME"]) == {"done": ["w01-item-01", "w01-item-02"], "quarantined": ["w01-item-03"]}
    assert json.loads(env[ADOPT_SECRETS])["session_token"] == "bst_" + "2" * 43  # its own fresh credentials
    assert "BURN_RESUME" in argv and "w01-item-03" not in " ".join(argv)
    assert logged == ["replaced agent-worker-000001 with agent-worker-000002: 2 done, 1 quarantined"]
    assert swarm.workers[new].task == OLD_TASK and swarm.workers[new].session_id == "sess_2"
    assert swarm.names == [old, new]  # both are removed when the launcher stops


@pytest.mark.parametrize("instance, says", [
    ({"session_id": "sess_1", "status": "exited", "replacement": None}, "exited"),
    (burned("needs_person"), "needs a person"),
    (burned("linked", replaced_by="sess_9"), "already replaced"),
    ({"session_id": "sess_1", "status": "active", "replacement": None}, "still active"),
])
def test_a_worker_that_is_not_pending_is_not_restarted(instance, says):
    swarm, burn, docker, logged, slept = swarm_of(count=1, items=4)
    burn.reads["sess_1"] = [instance]
    assert swarm.on_exit(swarm.names[0]) is None
    assert burn.made("tokens") == burn.calls[:1] and burn.made("join") == [burn.calls[1]]  # only the first ones
    assert len(docker.runs) == 1 and slept == []
    (line,) = logged
    assert line.startswith("agent-worker-000001 ") and says in line and "not restarted" in line


def test_a_burned_worker_with_no_record_yet_is_read_three_times_over_about_ten_seconds():
    swarm, burn, docker, logged, slept = swarm_of(count=1, items=4)
    burn.reads["sess_1"] = [{"session_id": "sess_1", "status": "revoked", "replacement": None}]
    assert swarm.on_exit(swarm.names[0]) is None
    assert burn.calls.count(("GET", "/instances/sess_1", None)) == 3 and slept == [5.0, 5.0]
    assert len(docker.runs) == 1 and "no replacement record" in logged[0]


def test_a_record_written_just_after_the_exit_is_still_found():
    swarm, burn, docker, logged, slept = swarm_of(count=1, items=4)
    burn.reads["sess_1"] = [{"session_id": "sess_1", "status": "revoked", "replacement": None}, burned()]
    assert swarm.on_exit(swarm.names[0]) is not None
    assert slept == [5.0] and logged[0].startswith("replaced agent-worker-000001 with")


@pytest.mark.parametrize("failure", ["token", "join"])
def test_when_no_fresh_instance_can_join_it_stays_pending_and_nothing_retries(failure):
    swarm, burn, docker, logged, slept = swarm_of(count=1, items=4)
    burn.reads["sess_1"] = [burned()]
    error = ApiError("Join token expired", 403)
    if failure == "token":
        burn.token_error = error
    else:
        burn.join_error = error
    assert swarm.on_exit(swarm.names[0]) is None
    assert len(burn.made("tokens")) == 2 and len(burn.made("join")) == (1 if failure == "token" else 2)
    assert not any(c[1].endswith("/replace") for c in burn.calls) and len(docker.runs) == 1 and slept == []
    (line,) = logged
    assert "agent-worker-000001" in line and "Join token expired" in line and "stays pending" in line


@pytest.mark.parametrize("error, says", [
    (ApiError("the new instance must be created after the old one was burned", 400), "it stays pending"),
    (ApiError("Instance has no pending replacement (linked)", 409), "no longer pending: not restarted"),
])
def test_when_burn_refuses_the_link_the_fresh_instance_is_ended_and_nothing_starts(error, says):
    swarm, burn, docker, logged, _ = swarm_of(count=1, items=4)
    burn.reads["sess_1"] = [burned()]
    burn.replace_error = error
    assert swarm.on_exit(swarm.names[0]) is None
    assert burn.calls[-1] == ("exit", "sess_2", "bst_" + "2" * 43)  # with its own token
    assert len(burn.made("join")) == 2 and len(docker.runs) == 1  # no second try
    (line,) = logged
    assert says in line and str(error) in line and "pending" in line


def test_a_replacement_is_itself_replaced_when_it_is_burned():
    swarm, burn, docker, logged, _ = swarm_of(count=1, items=4)
    burn.reads["sess_1"] = [burned()]
    second = swarm.on_exit(swarm.names[0])
    burn.reads["sess_2"] = [burned(session_id="sess_2", generation=1)]
    burn.linked = {"done": ["w01-item-01", "w01-item-02", "w01-item-04"], "quarantined": ["w01-item-03"]}
    third = swarm.on_exit(second)
    assert third not in (None, second) and json.loads(docker.runs[-1][1]["BURN_TASK"]) == OLD_TASK
    assert logged[-1] == "replaced agent-worker-000002 with agent-worker-000003: 3 done, 1 quarantined"


# --- the follow loop and the command line -------------------------------------------------------------------------

def test_follow_tails_each_worker_and_then_the_one_that_replaced_it():
    tailed, replaced = [], {"a": "a2"}

    def on_exit(name):
        return replaced.pop(name, None)

    run_swarm.follow(["a", "b"], on_exit, tail=tailed.append)
    assert sorted(tailed) == ["a", "a2", "b"] and replaced == {}


def run_main(monkeypatch, *args):
    captured = {}

    class Started:
        names: list = []

        def on_exit(self, name):
            return None

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
