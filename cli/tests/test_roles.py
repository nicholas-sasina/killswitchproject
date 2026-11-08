"""burn run without --role or a join token: the role named after the command, get-or-create, the minted token."""
import json
import stat
import sys
import time
from pathlib import Path

import pytest

from burn_cli import roles, signin
from burn_cli.api import ApiError
from burn_cli.main import build_parser, run as cli
from fake_api import API_TOKEN
from fake_cognito import jwt
from test_run import stack  # noqa: F401 - a fixture

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "mcp-tools.json"
# The script an agent runs: it writes its argv and environment where the test can read them.
AGENT = "import json, os, sys; json.dump({'argv': sys.argv, 'env': dict(os.environ)}, open(sys.argv[1], 'w'))"


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


# -- burn run ------------------------------------------------------------------------------------
def agent(stack, name="agent.py") -> Path:
    path = stack.tmp / name
    path.write_text(AGENT)
    return path


def minted(stack) -> list[str]:
    return [f"bjt_{token_id}.{row['secret']}" for token_id, row in stack.tokens.items()]


def everything_seen(capfd, out: Path) -> str:
    """All a person or the agent could see: the CLI's output and the agent's own argv and environment."""
    printed = capfd.readouterr()
    return printed.out + printed.err + json.dumps(json.loads(out.read_text()))


def test_signed_in_run_creates_the_role_and_joins_with_a_one_use_token_it_never_shows(stack, capfd, monkeypatch):
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    before = set(minted(stack))
    out = stack.tmp / "seen.json"
    assert cli(["run", sys.executable, str(agent(stack)), str(out)]) == 0
    seen = capfd.readouterr()
    agent_saw = json.loads(out.read_text())
    (token,) = set(minted(stack)) - before
    assert stack.roles["agent"] == {**roles.full_access("agent"), "managed_by": "ui"}
    assert stack.calls("POST", "/roles/agent/tokens")[0][2] == {"uses": 1, "ttl": roles.JOIN_TTL}
    assert stack.calls("POST", "/instances")[0][2] == {"role": "agent", "enroll_token": token}
    assert 'role "agent" (named after the command' in seen.err and 'created role "agent" with full access' in seen.err
    assert agent_saw["env"]["BURN_ROLE"] == "agent"
    for text in (seen.out, seen.err, json.dumps(agent_saw)):
        assert token not in text and token.split(".")[1] not in text and API_TOKEN not in text


def test_reusing_a_limited_role_says_it_is_limited(stack, capfd, monkeypatch):
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    worker = dict(stack.roles["worker"])
    out = stack.tmp / "seen.json"
    assert cli(["run", "--role", "worker", sys.executable, str(agent(stack)), str(out)]) == 0
    err = capfd.readouterr().err
    assert 'reusing role "worker" as it is (Limited, not Full access: tools read_inbox' in err
    assert stack.roles["worker"] == worker and stack.calls("POST", "/roles") == []
    assert "named after the command" not in err  # --role given: nothing derived


def test_reusing_a_full_access_role_says_so(stack, capfd, monkeypatch):
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    stack.roles["claude"] = {**roles.full_access("claude"), "managed_by": "ui"}
    claude = stack.tmp / "claude"
    claude.write_text(f"#!{sys.executable}\n{AGENT}\n")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    assert cli(["run", str(claude), str(stack.tmp / "seen.json"), "--dangerously-skip-permissions"]) == 0
    err = capfd.readouterr().err
    assert 'role "claude"' in err and 'reusing role "claude" as it is (Full access)' in err
    assert json.loads((stack.tmp / "seen.json").read_text())["argv"][2:] == ["--dangerously-skip-permissions"]


def test_a_mistyped_role_stops_before_anything_is_created_or_minted(stack, capsys, monkeypatch):
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    before = dict(stack.tokens)
    assert cli(["run", "--role", "raeder", "claude"]) == 2
    assert 'role "raeder" does not exist' in capsys.readouterr().err
    assert "raeder" not in stack.roles and stack.calls("POST", "/roles") == [] and stack.tokens == before
    assert stack.calls("POST", "/instances") == []


def test_with_sign_in_on_the_persons_own_token_makes_the_calls(stack, capfd, monkeypatch):
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    monkeypatch.delenv("BURN_LOCAL_DEV")
    monkeypatch.setenv("BURN_PEOPLE_ENABLED", "on")
    id_token = jwt({"email": "ada@example.com", "exp": int(time.time()) + 3600})
    signin.save_cache({"id_token": id_token, "refresh_token": "r", "expires_at": int(time.time()) + 3600,
                       "email": "ada@example.com", "domain": "http://127.0.0.1:9", "client_id": "c"})
    stack.person_token = id_token
    out = stack.tmp / "seen.json"
    assert cli(["run", sys.executable, str(agent(stack)), str(out)]) == 0
    for method, path in (("GET", "/roles/agent"), ("POST", "/roles"), ("POST", "/roles/agent/tokens")):
        (call,) = stack.calls(method, path)
        assert call[3].get("Authorization") == f"Bearer {id_token}" and "x-burn-demo-token" not in {
            k.lower() for k in call[3]}
    assert id_token not in everything_seen(capfd, out)


@pytest.mark.parametrize("signed_in_stack", [True, False])
def test_with_neither_a_token_nor_a_sign_in_the_error_names_both_ways(stack, capsys, monkeypatch, signed_in_stack):
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    if signed_in_stack:  # the stack requires sign-in, and nobody has signed in
        monkeypatch.delenv("BURN_LOCAL_DEV")
        monkeypatch.setenv("BURN_PEOPLE_ENABLED", "on")
    else:  # local development without the demo token
        monkeypatch.delenv("BURN_DEMO_TOKEN")
    assert cli(["run", "claude"]) == 2
    err = capsys.readouterr().err
    assert "`burn login`" in err and "BURN_JOIN_TOKEN" in err and "--token" in err
    assert stack.requests == []


def test_a_given_join_token_still_wins(stack, capfd):
    out = stack.tmp / "seen.json"
    assert cli(["run", "--role", "worker", sys.executable, str(agent(stack)), str(out)]) == 0
    assert stack.calls("GET", "/roles/worker") == [] and stack.calls("POST", "/roles/worker/tokens") == []
    assert stack.calls("POST", "/instances")[0][2] == {"role": "worker", "enroll_token": stack.token}
    assert "reusing" not in capfd.readouterr().err


def test_a_derived_name_kept_for_the_eval_harness_stops_before_any_call(stack, capsys, monkeypatch):
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    assert cli(["run", "python", "eval.py"]) == 2
    assert "eval harness: pass --role" in capsys.readouterr().err and stack.requests == []


# -- flags after the command ---------------------------------------------------------------------
def test_flags_after_the_command_reach_it_without_a_double_dash():
    args = build_parser().parse_args(["run", "claude", "--dangerously-skip-permissions", "--role", "x", "--token", "t"])
    assert args.cmd == ["claude", "--dangerously-skip-permissions", "--role", "x", "--token", "t"]
    assert args.role is None and args.token is None


def test_a_command_flag_named_like_burn_runs_own_is_the_commands(stack, capfd):
    out = stack.tmp / "seen.json"
    argv = ["run", "--role", "worker", sys.executable, str(agent(stack)), str(out), "--token", "not-a-token", "--role", "x"]
    assert cli(argv) == 0
    assert json.loads(out.read_text())["argv"][2:] == ["--token", "not-a-token", "--role", "x"]
    assert stack.calls("POST", "/instances")[0][2]["enroll_token"] == stack.token
