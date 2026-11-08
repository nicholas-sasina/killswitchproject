"""Harness presets: what `burn run claude` sets up in the instance's environment and new HOME.

Every run here is a real Instance against the stub API. The `claude` it starts is a stand-in script of that name
that records its environment and HOME, so no test reaches a model, AWS or the network.
"""
import json
import os
import sys

import pytest

from burn_cli.runner import harness
from burn_cli.runner.credentials import from_secrets
from burn_cli.runner.env import child_env
from test_run import HOST_SECRETS, instance, py, stack  # noqa: F401 - stack is a fixture

MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Records the environment, the working directory and every file in HOME with its mode, as JSON in argv[1].
RECORD = """
import json, os, stat, sys
home = os.environ["HOME"]
files = {}
for root, dirs, names in os.walk(home):
    for name in dirs + names:
        path = os.path.join(root, name)
        rel = os.path.relpath(path, home)
        files[rel] = {"mode": stat.S_IMODE(os.lstat(path).st_mode),
                      "text": open(path).read() if os.path.isfile(path) else None}
json.dump({"env": dict(os.environ), "cwd": os.getcwd(), "files": files}, open(sys.argv[1], "w"))
"""


@pytest.fixture
def fake_claude(tmp_path):
    """An executable named `claude` that records what it was started with."""
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(f"#!{sys.executable}\n{RECORD}")
    path.chmod(0o755)
    return str(path)


def run_claude(stack, fake_claude, **kw):
    out = stack.tmp / "claude.json"
    inst = instance(stack, [fake_claude, str(out)], **kw)
    inst.host_env.update({"HOME": str(stack.tmp / "realhome"), "AWS_REGION": "eu-west-1"})
    assert inst.run() == 0
    return json.loads(out.read_text())


# -- choosing a preset ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("command", [["claude"], ["claude", "-p", "hi"], ["/usr/local/bin/claude", "--resume"]])
def test_claude_is_chosen_by_the_commands_basename(command):
    assert harness.preset(command) is harness.CLAUDE


@pytest.mark.parametrize("command", [[], ["python", "agent.py"], ["env", "claude"], ["claude-code"], ["./claude.sh"],
                                     ["opencode"], ["codex"]])
def test_other_commands_get_no_preset(command):
    assert harness.preset(command) is None


def test_no_preset_leaves_the_environment_exactly_as_before():
    creds = from_secrets({"badge": {"client_id": "c", "client_secret": "s", "token_url": "u"},
                          "aws_key": {"access_key_id": "AKIAOWN", "secret_access_key": "own"}})
    common = dict(session={"session_id": "s", "agent_id": "agent-a"}, credentials=creds, mcp_url="m", spawn_url="v",
                  home="/h", local_token="t")
    host = {"PATH": "/bin", "AWS_REGION": "eu-west-1", **HOST_SECRETS}
    assert child_env(host, preset_env=None, allow=(), **common) == child_env(host, **common)


def test_a_command_without_a_preset_starts_with_an_empty_home(stack):
    out = stack.tmp / "plain.json"
    assert instance(stack, py(RECORD, str(out))).run() == 0
    seen = json.loads(out.read_text())
    assert seen["files"] == {}
    assert not [k for k in seen["env"] if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_"))]
    assert seen["env"]["AWS_REGION"] == "us-east-1"  # the host's, as before


# -- the claude preset ---------------------------------------------------------------------------------------
def test_claude_gets_bedrock_with_the_instances_own_key(stack, fake_claude):
    env = run_claude(stack, fake_claude)["env"]
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "us-east-1"  # the stack's region, over the host's eu-west-1
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        assert env[name] == MODEL, name
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["ENABLE_TOOL_SEARCH"] == "false"  # Burn's tools are sent with every request, never deferred
    assert env["AWS_ACCESS_KEY_ID"].startswith("AKIA") and env["AWS_ACCESS_KEY_ID"] != HOST_SECRETS["AWS_ACCESS_KEY_ID"]
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_API_KEY"):
        assert name not in env, name


def test_the_presets_variables_hold_no_secret():
    assert set(harness.CLAUDE.env) & {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                                      "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} == set()
    assert not [k for k in harness.CLAUDE.env if k.startswith("BURN_")]


def test_claude_home_connects_burns_tools_and_skips_every_first_run_prompt(stack, fake_claude):
    seen = run_claude(stack, fake_claude)
    config = json.loads(seen["files"][".claude.json"]["text"])
    assert config["hasCompletedOnboarding"] is True
    assert config["projects"] == {seen["cwd"]: {"hasTrustDialogAccepted": True}}  # only where it runs
    assert config["mcpServers"] == {"burn": {"type": "http", "url": "${BURN_MCP_URL}",
                                             "headers": {"Authorization": "Bearer ${BURN_LOCAL_TOKEN}"}}}
    settings = json.loads(seen["files"][".claude/settings.json"]["text"])
    assert settings == {"permissions": {"allow": ["mcp__burn"]}}
    # Claude Code expands the references from the variables burn run sets
    assert seen["env"]["BURN_MCP_URL"].startswith("http://127.0.0.1:") and seen["env"]["BURN_LOCAL_TOKEN"]


def test_claude_home_files_hold_no_secret_and_are_private(stack, fake_claude):
    seen = run_claude(stack, fake_claude)
    env, files = seen["env"], seen["files"]
    text = "".join(f["text"] or "" for f in files.values())
    for value in (env["BURN_LOCAL_TOKEN"], env["BURN_MCP_URL"], env["AWS_SECRET_ACCESS_KEY"], env["BURN_API_KEY"],
                  stack.token, *(v for v in HOST_SECRETS.values() if len(v) > 2)):  # "b" is in any text
        assert value not in text
    assert {rel: f["mode"] for rel, f in files.items()} == {".claude.json": 0o600, ".claude": 0o700,
                                                          ".claude/settings.json": 0o600}


def test_claude_home_is_removed_at_exit_and_the_real_home_is_untouched(stack, fake_claude):
    real = stack.tmp / "realhome"
    (real / ".claude").mkdir(parents=True)
    (real / ".claude.json").write_text('{"mine": true}')
    seen = run_claude(stack, fake_claude)
    assert not os.path.exists(seen["env"]["HOME"])
    assert (real / ".claude.json").read_text() == '{"mine": true}' and os.listdir(real / ".claude") == []


def test_preset_files_never_replace_what_is_already_there(tmp_path):
    (tmp_path / ".claude.json").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(FileExistsError):
        harness.write_files(harness.CLAUDE, str(tmp_path), "/work")
    assert not (tmp_path / "elsewhere").exists()


def test_preset_files_are_chowned_to_the_agent_user(stack, fake_claude, monkeypatch):
    import burn_cli.runner.run as runmod
    from burn_cli.runner import privs
    from test_run import FakePopen
    chowned = []
    monkeypatch.setattr(runmod.os, "chown", lambda path, uid, gid: chowned.append((path, uid, gid)))
    monkeypatch.setattr(runmod.subprocess, "Popen", FakePopen)
    out = stack.tmp / "claude.json"
    assert instance(stack, [fake_claude, str(out)], drop=privs.Drop("agent", 4242, 4343)).run() == 0
    home = chowned[0][0]
    assert {(os.path.relpath(p, home), u, g) for p, u, g in chowned} == {
        (".", 4242, 4343), (".claude.json", 4242, 4343), (".claude", 4242, 4343), (".claude/settings.json", 4242, 4343)}


def test_burn_run_says_it_used_the_preset(stack, fake_claude, capfd):
    run_claude(stack, fake_claude)
    err = capfd.readouterr().err
    assert "burn: claude preset: Claude Code on Bedrock" in err and "no aws_key" not in err


def test_a_role_without_an_aws_key_is_told_claude_has_no_model(stack, fake_claude, capfd):
    stack.roles["worker"]["credentials"] = ["api_key"]
    env = run_claude(stack, fake_claude)["env"]
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "role worker has no aws_key, so claude has no model" in capfd.readouterr().err


def test_allow_env_wins_over_a_presets_default(stack, fake_claude):
    out = stack.tmp / "claude.json"
    inst = instance(stack, [fake_claude, str(out)], allow_env=("ANTHROPIC_MODEL",))
    inst.host_env["ANTHROPIC_MODEL"] = "my-model"
    assert inst.run() == 0
    assert json.loads(out.read_text())["env"]["ANTHROPIC_MODEL"] == "my-model"
