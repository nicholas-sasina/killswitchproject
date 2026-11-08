import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_swarm  # noqa: E402
import swarm_launch  # noqa: E402
from swarm_launch import ADOPT_SECRETS, docker_run_argv, start_workers, to_container_url  # noqa: E402

ADMIN = "admin-token-value-xyz"
JOIN = "bjt_" + "0123456789abcdef" + "." + "J" * 43


class FakeApi:
    """The admin client (only the join-token call) and the instance client (join and exit), recording both."""

    def __init__(self, omit_secrets=False):
        self.calls, self.n, self.omit_secrets = [], 0, omit_secrets

    def request(self, method, path, body=None):  # the admin client
        self.calls.append(("admin", method, path, body))
        assert path.endswith("/tokens"), "the admin token is for creating the join token only"
        return {"token": JOIN}

    def join(self, role, enroll_token):  # the instance client: no auth header
        self.calls.append(("instance", "POST", "/instances", {"role": role, "enroll_token": enroll_token}))
        self.n += 1
        reply = {"session_id": f"sess_{self.n}", "agent_id": f"agent-worker-{self.n:06x}", "role": "worker",
                 "tools": ["read_inbox"]}
        if not self.omit_secrets:
            reply["secrets"] = {"badge": {"client_id": f"cid{self.n}", "client_secret": f"badge-secret-{self.n}",
                                          "token_url": "http://localhost:8700/mock-token", "scope": "s"},
                                "api_key": f"mpk_live_secret{self.n}",
                                "aws_key": {"access_key_id": f"AKIASECRET{self.n}", "secret_access_key": f"aws-secret-{self.n}"},
                                "session_token": "bst_" + f"{self.n}" * 43}
        return reply

    def exit(self, session_id, session_token):
        self.calls.append(("instance", "POST", f"/instances/{session_id}/exit", session_token))
        return {}


class Docker:
    def __init__(self, fail_on=None):
        self.runs, self.fail_on = [], fail_on

    def __call__(self, argv, env):
        if self.fail_on and len(self.runs) + 1 == self.fail_on and argv[1] == "run":
            raise SystemExit("docker run failed")
        self.runs.append((argv, env))


def launch(api=None, docker=None, mock=True, count=3):
    api, docker = api or FakeApi(), docker or Docker()
    names = start_workers(api, api, "worker", count, api_url="http://localhost:8700",
                          gateway_url="http://localhost:8701/mcp", mock=mock, interval=3.0,
                          certs_dir="/repo/.run/proxy-certs", docker=docker)
    return api, docker, names


def test_each_container_is_joined_once_with_one_token_and_gets_only_its_own_values():
    api, docker, names = launch(count=3)
    assert api.calls[0] == ("admin", "POST", "/roles/worker/tokens", {"uses": 3, "ttl": 3600})
    assert [c[0] for c in api.calls[1:]] == ["instance"] * 3  # the joins go through the instance client
    assert all(c[3] == {"role": "worker", "enroll_token": JOIN} for c in api.calls[1:]) and len(names) == len(set(names)) == 3
    secrets = [json.loads(env[ADOPT_SECRETS]) for _, env in docker.runs]
    assert [s["badge"]["client_id"] for s in secrets] == ["cid1", "cid2", "cid3"]
    for i, (_, env) in enumerate(docker.runs, start=1):
        assert f"aws-secret-{i}" in env[ADOPT_SECRETS] and all(f"aws-secret-{j}" not in env[ADOPT_SECRETS] for j in range(1, 4) if j != i)


def test_no_secret_value_is_in_any_docker_argv_and_values_are_not_even_given_as_name_equals_value():
    api, docker, _ = launch(count=3)
    values = [ADMIN, JOIN]
    assert ADMIN not in json.dumps(docker.runs)  # the launcher never even hands the admin token to docker
    for _, env in docker.runs:
        s = json.loads(env[ADOPT_SECRETS])
        values += [s["badge"]["client_secret"], s["api_key"], s["aws_key"]["secret_access_key"],
                   s["aws_key"]["access_key_id"], s["session_token"]]
    for argv, env in docker.runs:
        joined = " ".join(argv)
        for value in values:
            assert value not in joined
        names = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
        assert ADOPT_SECRETS in names and "BURN_DEMO_TOKEN" not in names
        assert all("=" not in n for n in names), "environment must be passed by name only"
        assert set(names) == set(env)  # everything the container is given comes from the launcher's environment
        assert "BURN_DEMO_TOKEN" not in env  # a worker container never holds the admin token
        assert JOIN not in json.dumps(env)  # the join token is used on the host and goes no further


def test_the_session_in_argv_has_no_secrets_field():
    _, docker, _ = launch(count=1)
    argv = docker.runs[0][0]
    public = json.loads(argv[argv.index("--adopt") + 1])
    assert "secrets" not in public and public["session_id"] == "sess_1"
    assert argv[argv.index("--") + 1:] == ["python", "-m", "burn_agents.swarm_worker"]


def test_mock_mode_points_containers_at_the_host_and_has_no_proxy_or_network():
    _, docker, _ = launch(count=1)
    argv, env = docker.runs[0]
    assert env["BURN_API_URL"] == "http://host.docker.internal:8700"
    assert env["BURN_GATEWAY_URL"] == "http://host.docker.internal:8701/mcp"
    assert json.loads(env[ADOPT_SECRETS])["badge"]["token_url"] == "http://host.docker.internal:8700/mock-token"
    assert "--add-host" in argv and "--network" not in argv and "HTTPS_PROXY" not in env


def test_real_mode_uses_the_internal_network_and_gives_the_supervisor_its_proxy_identity():
    _, docker, _ = launch(mock=False, count=1)
    argv, env = docker.runs[0]
    assert argv[argv.index("--network") + 1] == swarm_launch.NETWORK and "--add-host" not in argv
    assert "/repo/.run/proxy-certs:/certs:ro" in argv
    assert env["BURN_API_URL"] == "http://localhost:8700"  # unchanged: the caller passes the real URLs
    public = json.loads(argv[argv.index("--adopt") + 1])
    assert env["HTTPS_PROXY"] == f"http://{public['agent_id']}:{public['session_id']}@proxy:8080"
    assert env["BURN_PROXY_URL"] == "http://proxy:8080"


def test_an_api_without_secrets_fails_clearly_and_ends_the_session_it_started():
    api = FakeApi(omit_secrets=True)
    with pytest.raises(ValueError, match="API too old: no secrets in the join reply"):
        launch(api=api)
    assert ("instance", "POST", "/instances/sess_1/exit", "") in api.calls  # no token to end it with, but it is tried


def test_a_docker_failure_ends_the_unstarted_session_and_removes_the_started_workers():
    api, docker = FakeApi(), Docker(fail_on=2)
    with pytest.raises(SystemExit):
        launch(api=api, docker=docker, count=3)
    ended = [c for c in api.calls if c[2].endswith("/exit")]
    assert [(c[2], c[3]) for c in ended] == [("/instances/sess_2/exit", "bst_" + "2" * 43)]  # its own token
    assert [r[0][:3] for r in docker.runs] == [["docker", "run", "-d"], ["docker", "rm", "-f"]]  # the one that started is removed


def test_container_urls():
    assert to_container_url("http://localhost:8700/x") == "http://host.docker.internal:8700/x"
    assert to_container_url("http://127.0.0.1:8701/mcp") == "http://host.docker.internal:8701/mcp"
    assert to_container_url("https://api.example.com/x") == "https://api.example.com/x"


def test_argv_helper_never_embeds_a_value():
    argv = docker_run_argv("n", "worker", {"session_id": "s"}, ["A", "B"], mock=True)
    assert argv.count("-e") == 2 and "A" in argv and "B" in argv


def run_main(monkeypatch, *args):
    captured = {}
    monkeypatch.setattr(sys, "argv", ["run_swarm.py", "--role", "worker", "--count", "2", *args])
    monkeypatch.setattr(run_swarm, "docker", lambda *a, **k: None)
    monkeypatch.setattr(run_swarm, "start_workers", lambda api, instances, role, count, **kw: captured.update(kw, api=api, instances=instances) or [])
    run_swarm.main()
    return captured


def test_mock_swarm_ignores_the_environment_and_never_uses_a_real_token(monkeypatch):
    monkeypatch.setenv("BURN_API_URL", "https://real.example.com")
    monkeypatch.setenv("BURN_DEMO_TOKEN", "real-admin-token")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gateway.example.com/mcp")
    got = run_main(monkeypatch, "--mock")
    assert got["api_url"] == "http://localhost:8700" and got["mock"] is True
    assert "real-admin-token" not in repr(vars(got["api"])) and got["api"]._token == "mock"
    assert got["gateway_url"] == "http://localhost:8701/mcp"


def test_mock_swarm_refuses_a_non_local_api_url(monkeypatch):
    with pytest.raises(SystemExit, match="only talks to servers on localhost"):
        run_main(monkeypatch, "--mock", "--api-url", "https://api.example.com")


def test_real_swarm_reads_the_environment_and_needs_the_stack(monkeypatch):
    for name in ("BURN_API_URL", "BURN_DEMO_TOKEN", "BURN_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    with pytest.raises(SystemExit, match="make env"):
        run_main(monkeypatch)
    monkeypatch.setenv("BURN_API_URL", "https://api.example.com")
    monkeypatch.setenv("BURN_DEMO_TOKEN", "t")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gw.example.com/mcp")
    monkeypatch.setattr(run_swarm.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1})())
    with pytest.raises(SystemExit, match="start the stack first"):
        run_main(monkeypatch)
