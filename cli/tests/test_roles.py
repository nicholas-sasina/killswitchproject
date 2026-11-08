"""The role burn run names after a command, and get-or-create with full access."""
import json
from pathlib import Path

import pytest

from burn_cli import roles
from burn_cli.api import ApiError

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "mcp-tools.json"


# -- the full access body ---------------------------------------------------------------------------
def test_full_access_lists_every_tool_in_the_contract():
    contract = [tool["name"] for tool in json.loads(CONTRACT.read_text())["tools"]]
    assert sorted(roles.FULL_ACCESS_TOOLS) == sorted(contract)
    assert len(roles.FULL_ACCESS_TOOLS) == len(set(roles.FULL_ACCESS_TOOLS))


def test_full_access_is_every_tool_its_own_aws_key_and_no_egress_narrowing():
    assert roles.full_access("claude") == {
        "name": "claude", "tools": list(roles.FULL_ACCESS_TOOLS), "credentials": ["aws_key"], "may_message": [],
        "may_spawn": [], "egress": None, "owner": None, "canaries": True, "auto_burn": True, "replace_on_burn": False}


def test_full_access_and_limited_are_told_apart():
    assert roles.is_full_access(roles.full_access("a"))
    assert not roles.is_full_access({**roles.full_access("a"), "tools": ["read_inbox"]})
    assert not roles.is_full_access({**roles.full_access("a"), "egress": ["bedrock-runtime.us-east-1.amazonaws.com"]})
    assert roles.describe(roles.full_access("a")) == "Full access"
    assert roles.describe({"tools": ["read_inbox"], "egress": []}) == \
        "Limited, not Full access: tools read_inbox; egress none"


# -- the role named after the command -------------------------------------------------------------
@pytest.mark.parametrize("command, name", [
    (["claude", "--dangerously-skip-permissions"], "claude"),
    (["/usr/local/bin/opencode"], "opencode"),
    (["codex", "exec", "hi"], "codex"),
    (["python", "agent.py"], "agent"),
    (["python3", "-u", "./bots/My_Agent.py", "--fast"], "my-agent"),
    (["/usr/bin/python3.12", "agent.py"], "agent"),
    (["python", "-m", "bots.triage"], "triage"),
    (["node", "worker.js"], "worker"),
    (["bun", "run", "agent.ts"], "agent"),
    (["deno", "run", "-A", "agent.ts"], "agent"),
    (["uv", "run", "agent.py"], "agent"),
    (["uv", "run", "python", "agent.py"], "agent"),
    (["uvx", "some-tool"], "some-tool"),
    (["npx", "@scope/agent"], "agent"),
    (["bash", "run-agent.sh"], "run-agent"),
    (["zsh", "./ci/Nightly Job.zsh"], "nightly-job"),
    (["My.Agent"], "my-agent"),
    (["a" * 40], "a" * 32),
    (["x" * 31 + "_tail"], "x" * 31),  # cut to 32, then the dangling "-" trimmed
])
def test_the_role_is_named_after_the_program_or_the_script_it_runs(command, name):
    assert roles.name_for(command) == name


@pytest.mark.parametrize("command", [["python", "-c", "print(1)"], ["node"], ["sh", "-c", "exec codex"], ["___"],
                                     ["python", "--version"], ["npx", "-y"]])
def test_a_command_with_no_usable_name_asks_for_a_role(command):
    with pytest.raises(roles.RoleError, match="pass --role"):
        roles.name_for(command)


@pytest.mark.parametrize("command", [["eval"], ["python", "eval.py"], ["uv", "run", "eval-harness.py"], ["./EVAL-x"]])
def test_a_name_kept_for_the_eval_harness_is_never_derived(command):
    with pytest.raises(roles.RoleError, match="eval harness: pass --role"):
        roles.name_for(command)


# -- get or create, against a stub API --------------------------------------------------------------
class StubApi:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        reply = self.replies.pop(0)
        if isinstance(reply, ApiError):
            raise reply
        return reply


def missing():
    return ApiError("GET /roles/claude failed with HTTP 404: Unknown role", 404)


def test_a_missing_role_is_created_with_full_access():
    api = StubApi(missing(), {"name": "claude", "managed_by": "ui"})
    assert roles.get_or_create(api, "claude", derived=True) == ({"name": "claude", "managed_by": "ui"}, True)
    assert api.calls == [("GET", "/roles/claude", None), ("POST", "/roles", roles.full_access("claude"))]


def test_a_role_named_with_role_is_never_created():
    api = StubApi(missing())
    with pytest.raises(roles.RoleError, match='role "raeder" does not exist: create it in the console or with '
                                              '`burn apply`, or leave out --role'):
        roles.get_or_create(api, "raeder", derived=False)
    assert api.calls == [("GET", "/roles/raeder", None)]


def test_an_existing_role_is_used_as_it_is():
    limited = {"name": "claude", "tools": ["read_inbox"], "egress": [], "managed_by": "code"}
    api = StubApi(limited)
    assert roles.get_or_create(api, "claude", derived=True) == (limited, False)
    assert [c[0] for c in api.calls] == ["GET"]  # never replaced, never widened


def test_a_role_another_run_created_first_is_reused():
    api = StubApi(missing(), ApiError("POST /roles failed with HTTP 409: Role exists", 409), {"name": "claude"})
    assert roles.get_or_create(api, "claude", derived=True) == ({"name": "claude"}, False)
    assert [c[:2] for c in api.calls] == [("GET", "/roles/claude"), ("POST", "/roles"), ("GET", "/roles/claude")]


def test_a_scenario_agents_role_is_never_taken_by_a_derived_name():
    scenario = {"name": "reader", "managed_by": "scenario"}
    with pytest.raises(roles.RoleError, match="scenario agent: pass --role"):
        roles.get_or_create(StubApi(scenario), "reader", derived=True)
    assert roles.get_or_create(StubApi(scenario), "reader", derived=False) == (scenario, False)  # named on purpose


@pytest.mark.parametrize("status", [401, 403, 500])
def test_other_errors_are_not_mistaken_for_a_missing_role(status):
    api = StubApi(ApiError("no", status))
    with pytest.raises(ApiError):
        roles.get_or_create(api, "claude", derived=True)
    assert len(api.calls) == 1
