"""The launcher's side of AI workers: the role, the loaded scenario, and the run's cap on model calls. No network."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_swarm  # noqa: E402
import swarm_launch  # noqa: E402
from burn_cli.api import ApiError  # noqa: E402
from swarm_launch import MIN_SHARE, RoleError, RunBudget, ensure_role, full_access_role, start_workers  # noqa: E402
from test_swarm_replace import IMAGE, Docker, FakeBurn, burned  # noqa: E402

CONTRACT_TOOLS = [t["name"] for t in json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "mcp-tools.json").read_text())["tools"]]


class Roles:
    """GET /roles/{name} and POST /roles, recording calls. `role` is what exists (None: 404)."""

    def __init__(self, role=None, error=None, listed=()):
        self.role, self.error, self.listed, self.calls = role, error, list(listed), []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.error:
            raise self.error
        if (method, path) == ("GET", "/roles"):
            return {"roles": self.listed}
        if method == "GET":
            if self.role is None:
                raise ApiError("GET failed with HTTP 404: Unknown role", 404)
            return self.role
        if (method, path) == ("POST", "/roles"):
            return {**body, "managed_by": "ui", "active_instances": 0}
        raise AssertionError((method, path))


# --- the role --------------------------------------------------------------------------------------------------------

def test_the_full_access_body_lists_every_contract_tool_and_gives_an_own_aws_key():
    body = full_access_role("ai-swarm-check")
    assert body == {"name": "ai-swarm-check", "tools": CONTRACT_TOOLS, "credentials": ["aws_key"], "may_message": [],
                    "may_spawn": [], "egress": None, "owner": None, "canaries": True, "auto_burn": True,
                    "replace_on_burn": True}
    assert len(CONTRACT_TOOLS) == 7


def test_a_missing_role_is_created_with_full_access():
    api, said = Roles(role=None), []
    ensure_role(api, "ai-swarm-check", log=said.append)
    assert api.calls == [("GET", "/roles/ai-swarm-check", None),
                         ("POST", "/roles", full_access_role("ai-swarm-check"))]
    assert said == ["created role ai-swarm-check: full access, its own AWS key per instance, replace on burn"]


def test_an_existing_role_with_its_own_aws_key_is_used_as_it_is():
    api, said = Roles(role={"name": "worker", "tools": CONTRACT_TOOLS, "credentials": ["aws_key"],
                            "replace_on_burn": True}), []
    ensure_role(api, "worker", log=said.append)
    assert [c[0] for c in api.calls] == ["GET"] and said == ["using the existing role worker"]


def test_an_existing_role_without_an_aws_key_stops_with_what_to_change():
    api = Roles(role={"name": "worker", "tools": CONTRACT_TOOLS, "credentials": ["api_key"]})
    with pytest.raises(RoleError, match=r'no AWS key of their own .*add "aws_key" to its credentials'):
        ensure_role(api, "worker", log=lambda line: None)
    assert [c[0] for c in api.calls] == ["GET"]  # nothing is changed


def test_an_existing_role_without_the_ticket_tools_stops():
    api = Roles(role={"name": "worker", "tools": ["read_inbox"], "credentials": ["aws_key"]})
    with pytest.raises(RoleError, match="does not allow list_tickets, read_ticket"):
        ensure_role(api, "worker", log=lambda line: None)


def test_an_api_failure_other_than_404_is_not_taken_as_a_missing_role():
    api = Roles(error=ApiError("GET failed with HTTP 401", 401))
    with pytest.raises(ApiError):
        ensure_role(api, "worker", log=lambda line: None)
    assert [c[0] for c in api.calls] == ["GET"]


# --- the loaded scenario -----------------------------------------------------------------------------------------------

def test_without_a_loaded_scenario_the_launcher_stops_and_names_the_command_that_loads_one():
    api = Roles(listed=[{"name": "worker", "managed_by": "ui"}])
    with pytest.raises(SystemExit, match=r"no scenario is loaded.*scripts/load_scenario.py scenarios/support-desk.yaml"):
        run_swarm.require_scenario(api)


def test_a_loaded_scenario_shows_as_its_agents_roles():
    api = Roles(listed=[{"name": "reader", "managed_by": "scenario"}, {"name": "worker", "managed_by": "ui"}])
    assert run_swarm.require_scenario(api) == ["reader"]


def test_a_real_swarm_with_no_scenario_stops_before_the_role_is_made_and_before_any_container(monkeypatch):
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    monkeypatch.setenv("BURN_API_URL", "https://api.example.com")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gw.example.com/mcp")
    monkeypatch.setattr(run_swarm, "automation_headers", lambda: {"authorization": "Bearer automation-id"})
    api = Roles(listed=[])
    monkeypatch.setattr(run_swarm, "PersonApi", lambda url, headers: api)
    done = []
    for name in ("docker", "fetch_proxy_ca", "start_workers", "ensure_role"):
        monkeypatch.setattr(run_swarm, name, lambda *a, _n=name, **k: done.append(_n))
    monkeypatch.setattr(sys, "argv", ["run_swarm.py", "--role", "ai-swarm-check", "--count", "2", "--items", "2"])
    with pytest.raises(SystemExit, match="no scenario is loaded"):
        run_swarm.main()
    assert done == [] and api.calls == [("GET", "/roles", None)]


def test_a_real_swarm_stops_on_a_role_without_an_aws_key_before_any_container(monkeypatch):
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: None)
    monkeypatch.setenv("BURN_API_URL", "https://api.example.com")
    monkeypatch.setenv("BURN_GATEWAY_URL", "https://gw.example.com/mcp")
    monkeypatch.setattr(run_swarm, "automation_headers", lambda: {"authorization": "Bearer automation-id"})
    api = Roles(role={"name": "worker", "tools": CONTRACT_TOOLS, "credentials": []},
                listed=[{"name": "reader", "managed_by": "scenario"}])
    monkeypatch.setattr(run_swarm, "PersonApi", lambda url, headers: api)
    done = []
    for name in ("docker", "fetch_proxy_ca", "start_workers"):
        monkeypatch.setattr(run_swarm, name, lambda *a, _n=name, **k: done.append(_n))
    monkeypatch.setattr(sys, "argv", ["run_swarm.py", "--role", "worker", "--count", "1"])
    with pytest.raises(SystemExit, match='add "aws_key"'):
        run_swarm.main()
    assert done == []


# --- the run's cap on model calls ----------------------------------------------------------------------------------------

def test_the_run_budget_shares_out_no_more_than_its_total():
    budget = RunBudget(100, 40, count=4)
    assert budget.per_worker == 25  # 100 for 4 workers: 25 each rather than 40
    shares = [budget.take() for _ in range(6)]
    assert shares == [25, 25, 25, 25, 0, 0] and budget.left == 0
    budget.give_back(10)
    assert budget.take() == 10 and budget.left == 0


@pytest.mark.parametrize("total, count", [(10, 4), (0, 1)])
def test_a_run_cap_too_small_for_the_workers_is_refused(total, count):
    with pytest.raises(ValueError, match="raise the run's cap or start fewer"):
        RunBudget(total, 40, count)


def test_a_run_cap_too_small_stops_the_launcher_before_anything(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_swarm.py", "--role", "worker", "--count", "20", "--max-model-calls", "50"])
    monkeypatch.setattr(run_swarm, "load_dotenv", lambda: (_ for _ in ()).throw(AssertionError("went on")))
    with pytest.raises(SystemExit, match="--max-model-calls"):
        run_swarm.main()


def ai_swarm(count=2, items=4, total=100, per_worker=40, burn=None):
    burn, docker, logged = burn or FakeBurn(), Docker(), []
    swarm = start_workers(burn, burn, "worker", count, api_url="https://api.example.com",
                          gateway_url="https://gw.example.com/mcp", mock=False, interval=3.0, certs_dir="/c",
                          docker=docker, items=items, sleep=lambda s: None, log=logged.append, image=IMAGE,
                          budget=RunBudget(total, per_worker, count))
    return swarm, burn, docker, logged


def model_calls(argv):
    return int(argv[argv.index("--max-model-calls") + 1])


def test_each_ai_worker_is_started_with_its_share_and_the_ai_worker_not_the_scripted_one():
    swarm, _, docker, _ = ai_swarm(count=2, total=100, per_worker=40)
    for argv, env in docker.runs:
        assert argv[argv.index("--") + 1:argv.index("--") + 4] == ["python", "-m", "burn_agents.swarm_worker"]
        assert "--scripted" not in argv and model_calls(argv) == 40
        assert env["AWS_CA_BUNDLE"] == "/certs/mitmproxy-ca-cert.pem"  # botocore trusts the proxy's CA through it
    assert swarm.budget.left == 20


def test_a_replacement_gets_what_the_burned_worker_did_not_use():
    swarm, burn, docker, logged = ai_swarm(count=2, total=80, per_worker=40)
    first = swarm.names[0]
    assert swarm.budget.left == 0
    swarm.observe(first, "2026-11-08 agent-worker-000001 model calls used: 13 of 40\n")
    burn.reads["sess_1"] = [burned()]
    replacement = swarm.on_exit(first)
    assert replacement and model_calls(docker.runs[-1][0]) == 27  # 40 - 13 came back to the run
    assert swarm.budget.left == 0


def test_a_worker_that_did_not_say_what_it_used_gives_nothing_back_so_its_replacement_waits_for_a_person():
    swarm, burn, docker, logged = ai_swarm(count=2, total=80, per_worker=40)
    burn.reads["sess_1"] = [burned()]
    joins = len(burn.made("join"))
    assert swarm.on_exit(swarm.names[0]) is None
    assert "the run's cap of 80 model calls is spent; it stays pending" in logged[-1]
    assert len(burn.made("join")) == joins and len(docker.runs) == 2  # no instance joined, no container started


def test_a_finished_worker_gives_its_unused_share_back_for_later_replacements():
    swarm, burn, docker, _ = ai_swarm(count=2, total=80, per_worker=40)
    done, other = swarm.names
    swarm.observe(done, "model calls used: 12 of 40")
    burn.reads["sess_1"] = [{"session_id": "sess_1", "status": "exited", "replacement": None}]
    assert swarm.on_exit(done) is None and swarm.budget.left == 28
    swarm.observe(other, "model calls used: 40 of 40")
    burn.reads["sess_2"] = [burned()]
    assert swarm.on_exit(other) and model_calls(docker.runs[-1][0]) == 28


def test_a_share_is_given_back_when_the_replacement_cannot_join():
    swarm, burn, _, _ = ai_swarm(count=1, total=80, per_worker=40)
    swarm.observe(swarm.names[0], "model calls used: 40 of 40")
    burn.reads["sess_1"] = [burned()]
    burn.join_error = ApiError("cannot reach the Burn API", None)
    assert swarm.on_exit(swarm.names[0]) is None and swarm.budget.left == 40


def test_a_mock_swarm_has_no_model_budget_and_runs_the_scripted_worker():
    from test_swarm_replace import swarm_of
    swarm, _, docker, _, _ = swarm_of(count=1)
    argv = docker.runs[0][0]
    assert swarm.budget is None and "--scripted" in argv and "--max-model-calls" not in argv


def test_a_real_swarm_without_a_budget_given_gets_the_safe_default():
    burn, docker = FakeBurn(), Docker()
    swarm = start_workers(burn, burn, "worker", 3, api_url="https://api.example.com", gateway_url="https://gw/mcp",
                          mock=False, interval=3.0, certs_dir="/c", docker=docker, image=IMAGE)
    assert swarm.budget.total == swarm_launch.DEFAULT_RUN_CALLS == 400
    assert [model_calls(a) for a, _ in docker.runs] == [40, 40, 40] and MIN_SHARE == 5


def test_the_launcher_reads_the_usage_line_from_the_logs_it_follows(monkeypatch):
    seen = []

    class Proc:
        stdout = iter(["a line\n", "model calls used: 3 of 40\n"])

    monkeypatch.setattr(run_swarm.subprocess, "Popen", lambda *a, **k: Proc())
    run_swarm.tail_logs("burn-swarm-worker-000001", lambda name, line: seen.append((name, line.strip())))
    assert seen[-1] == ("burn-swarm-worker-000001", "model calls used: 3 of 40")
