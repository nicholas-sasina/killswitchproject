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
    swarm = start_workers(api, api, "worker", count, api_url="http://localhost:8700",
                          gateway_url="http://localhost:8701/mcp", mock=mock, interval=3.0,
                          certs_dir="/repo/.run/proxy-certs", docker=docker, image="burn-swarm-worker:test")
    return api, docker, swarm.names


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
    # the mock servers have no model: the scripted worker, with its options on its command line
    assert argv[argv.index("--") + 1:] == ["python", "-m", "burn_agents.swarm_worker", "--scripted", "--interval", "3.0"]


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
    argv = docker_run_argv("n", "worker", {"session_id": "s"}, ["A", "B"], mock=True, image="burn-swarm-worker:t")
    assert argv.count("-e") == 2 and "A" in argv and "B" in argv


def run_main(monkeypatch, *args):
    captured = {}
    monkeypatch.setattr(sys, "argv", ["run_swarm.py", "--role", "worker", "--count", "2", *args])
    # A real swarm asks the API for the loaded scenario and the role; test_swarm_ai_launch.py covers both.
    monkeypatch.setattr(run_swarm, "require_scenario", lambda api: captured.setdefault("scenario_checked", True))
    monkeypatch.setattr(run_swarm, "ensure_role", lambda api, role: captured.setdefault("role_checked", role))
    monkeypatch.setattr(run_swarm, "docker", lambda *a, **k: None)
    monkeypatch.setattr(run_swarm, "fetch_proxy_ca", lambda docker_out, into: captured.setdefault("ca_into", into) and "/fetched/ca")
    monkeypatch.setattr(run_swarm, "remove_other_images", lambda keep: [])  # never touch this machine's images
    monkeypatch.setattr(run_swarm, "start_workers", lambda api, instances, role, count, **kw: captured.update(
        kw, api=api, instances=instances) or swarm_launch.Swarm(None, role, docker=None, **{
            k: kw[k] for k in ("api_url", "gateway_url", "mock", "interval", "certs_dir", "image")}))
    run_swarm.main()
    return captured


def test_mock_swarm_ignores_the_environment_and_never_uses_a_real_token(monkeypatch):
    monkeypatch.setenv("BURN_API_URL", "https://real.example.com")
    monkeypatch.setenv("BURN_DEMO_TOKEN", "real-admin-token")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gateway.example.com/mcp")
    got = run_main(monkeypatch, "--mock")
    assert got["api_url"] == "http://localhost:8700" and got["mock"] is True
    assert got["api"].auth_headers() == {"x-burn-demo-token": "mock"}  # never the environment's token
    assert got["gateway_url"] == "http://localhost:8701/mcp"


def test_mock_swarm_refuses_a_non_local_api_url(monkeypatch):
    with pytest.raises(SystemExit, match="only talks to servers on localhost"):
        run_main(monkeypatch, "--mock", "--api-url", "https://api.example.com")


def test_real_swarm_reads_the_environment_and_needs_the_stack(monkeypatch):
    for name in ("BURN_API_URL", "BURN_DEMO_TOKEN", "BURN_GATEWAY_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    # Signing in is the next tests' subject. Here it must not ask AWS whether the stack requires it.
    monkeypatch.setattr(run_swarm, "automation_headers", lambda: {"authorization": "Bearer automation-id"})
    with pytest.raises(SystemExit, match="make env"):
        run_main(monkeypatch)
    monkeypatch.setenv("BURN_API_URL", "https://api.example.com")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gw.example.com/mcp")  # no demo token needed
    monkeypatch.setattr(run_swarm.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1})())
    with pytest.raises(SystemExit, match="start the stack first"):
        run_main(monkeypatch)


def test_real_swarm_creates_the_join_token_as_the_automation_person(monkeypatch):
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    monkeypatch.setenv("BURN_API_URL", "https://api.example.com")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gw.example.com/mcp")
    monkeypatch.setenv("BURN_DEMO_TOKEN", "real-admin-token")
    monkeypatch.setattr(run_swarm, "automation_headers", lambda: {"authorization": "Bearer automation-id"})
    monkeypatch.setattr(run_swarm.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    got = run_main(monkeypatch)
    assert got["api"].auth_headers() == {"authorization": "Bearer automation-id"}
    assert got["api"].base_url == "https://api.example.com"


def test_real_swarm_stops_before_starting_anything_when_it_cannot_authenticate(monkeypatch):
    from burn_contracts.automation import AutomationAuthError
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    monkeypatch.setenv("BURN_API_URL", "https://api.example.com")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gw.example.com/mcp")

    def fail():
        raise AutomationAuthError("sign-in is off and there is no demo token (NoCredentialsError)")

    monkeypatch.setattr(run_swarm, "automation_headers", fail)
    monkeypatch.setattr(run_swarm.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    with pytest.raises(SystemExit, match="cannot authenticate to the Burn API: sign-in is off"):
        run_main(monkeypatch)


def test_real_swarm_with_sign_in_off_needs_no_aws(monkeypatch):
    from burn_contracts import automation

    def no_aws(service):
        raise AssertionError(f"made an AWS call ({service})")

    monkeypatch.setattr(automation, "_default", automation.AutomationPerson(client=no_aws))
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    for name, value in {"BURN_API_URL": "https://api.example.com", "BURN_GATEWAY_URL": "https://gw.example.com/mcp",
                        "BURN_PEOPLE_ENABLED": "off", "BURN_DEMO_TOKEN": "demo-from-dotenv"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("BURN_LOCAL_DEV", raising=False)
    monkeypatch.setattr(run_swarm.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    got = run_main(monkeypatch)
    assert got["api"].auth_headers() == {"x-burn-demo-token": "demo-from-dotenv"}


def test_the_person_client_sends_only_the_headers_it_is_given(monkeypatch):
    import io
    import burn_cli.api
    sent = []

    def urlopen(req, timeout):
        sent.append({k.lower(): v for k, v in req.header_items()})
        return io.BytesIO(json.dumps({"token": JOIN}).encode())

    monkeypatch.setattr(burn_cli.api.urllib.request, "urlopen", urlopen)
    reply = swarm_launch.PersonApi("https://api.example.invalid", lambda: {"authorization": "Bearer automation-id"}).request(
        "POST", "/roles/worker/tokens", {"uses": 1, "ttl": 60})
    assert reply == {"token": JOIN}
    (headers,) = sent
    assert headers["authorization"] == "Bearer automation-id" and "x-burn-demo-token" not in headers


# --- the proxy's CA comes from the running proxy, not from the launching checkout ---------------

class ProxyDocker:
    """Answers the two docker calls fetch_proxy_ca makes: find the proxy, copy its CA certificate out."""

    def __init__(self, proxies=("burn-demo-proxy-1",), cert="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"):
        self.proxies, self.cert, self.calls = list(proxies), cert, []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:2] == ["docker", "ps"]:
            return "\n".join(self.proxies) + ("\n" if self.proxies else "")
        if argv[:2] == ["docker", "cp"]:
            if self.cert is not None:
                Path(argv[3]).write_text(self.cert)
            return ""
        raise AssertionError(argv)


def test_the_ca_is_copied_from_the_running_proxy_and_only_its_public_certificate(tmp_path):
    from swarm_launch import NETWORK, fetch_proxy_ca
    docker = ProxyDocker()
    certs = fetch_proxy_ca(docker, str(tmp_path / "ca"))
    assert (Path(certs) / "mitmproxy-ca-cert.pem").read_text().startswith("-----BEGIN CERTIFICATE-----")
    ps, cp = docker.calls
    assert f"network={NETWORK}" in ps and "label=com.docker.compose.service=proxy" in ps
    assert cp[2] == "burn-demo-proxy-1:/certs/mitmproxy-ca-cert.pem"  # never mitmproxy-ca.pem, which holds the key
    assert sorted(p.name for p in Path(certs).iterdir()) == ["mitmproxy-ca-cert.pem"]


@pytest.mark.parametrize("proxies,message", [((), "no egress proxy"), (("a", "b"), "more than one")])
def test_no_single_running_proxy_stops_with_a_clear_message(tmp_path, proxies, message):
    from swarm_launch import ProxyCaError, fetch_proxy_ca
    with pytest.raises(ProxyCaError, match=message):
        fetch_proxy_ca(ProxyDocker(proxies=proxies), str(tmp_path / "ca"))


@pytest.mark.parametrize("cert", [None, "", "not a certificate"])
def test_a_proxy_without_a_ca_certificate_stops_with_a_clear_message(tmp_path, cert):
    from swarm_launch import ProxyCaError, fetch_proxy_ca
    with pytest.raises(ProxyCaError, match="CA certificate"):
        fetch_proxy_ca(ProxyDocker(cert=cert), str(tmp_path / "ca"))


def test_a_stale_certificate_from_an_earlier_run_is_replaced(tmp_path):
    from swarm_launch import fetch_proxy_ca
    old = tmp_path / "ca" / "mitmproxy-ca-cert.pem"
    old.parent.mkdir()
    old.write_text("-----BEGIN CERTIFICATE-----\nOLD\n-----END CERTIFICATE-----\n")
    fetch_proxy_ca(ProxyDocker(), str(tmp_path / "ca"))
    assert "OLD" not in old.read_text()


def _real_env(monkeypatch):
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    monkeypatch.setenv("BURN_API_URL", "https://api.example.com")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gw.example.com/mcp")
    monkeypatch.setattr(run_swarm, "automation_headers", lambda: {"authorization": "Bearer automation-id"})
    monkeypatch.setattr(run_swarm.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())


def test_a_real_swarm_mounts_the_ca_of_the_running_proxy_not_the_checkouts(monkeypatch):
    _real_env(monkeypatch)
    got = run_main(monkeypatch)
    assert got["certs_dir"] == "/fetched/ca"
    assert got["ca_into"].endswith("/.run/swarm-proxy-ca") and "proxy-certs" not in got["certs_dir"]


def test_a_real_swarm_without_the_proxy_ca_stops_before_starting_anything(monkeypatch):
    _real_env(monkeypatch)
    started = []
    monkeypatch.setattr(run_swarm, "require_scenario", lambda api: None)
    monkeypatch.setattr(run_swarm, "ensure_role", lambda api, role: None)

    def no_ca(docker_out, into):
        raise swarm_launch.ProxyCaError("no egress proxy is running on burn-demo_agents_internal")

    monkeypatch.setattr(sys, "argv", ["run_swarm.py", "--role", "worker", "--count", "1"])
    monkeypatch.setattr(run_swarm, "docker", lambda *a, **k: None)
    monkeypatch.setattr(run_swarm, "fetch_proxy_ca", no_ca)
    monkeypatch.setattr(run_swarm, "start_workers", lambda *a, **k: started.append(1) or [])
    with pytest.raises(SystemExit, match="no egress proxy is running"):
        run_swarm.main()
    assert started == []


def test_a_mock_swarm_needs_no_proxy_ca(monkeypatch):
    got = run_main(monkeypatch, "--mock")
    assert "ca_into" not in got and got["certs_dir"] == ""
