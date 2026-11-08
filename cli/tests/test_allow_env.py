"""--allow-env: a named host variable passes through to the agent, with a warning, and nothing burn run sets
itself can be passed. Every run is a real Instance against the stub API."""
import json

import pytest

from burn_cli.main import run as cli
from test_run import DUMP_ENV, instance, py, stack  # noqa: F401 - stack is a fixture


def test_allow_env_passes_a_host_variable_through_with_one_warning(stack, monkeypatch, capfd):
    out = stack.tmp / "env.json"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-passed")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-kept-back")
    code = cli(["run", "--role", "worker", "--allow-env", "OPENAI_API_KEY", "--allow-env", "OPENAI_API_KEY",
                "--", *py(DUMP_ENV, str(out))])
    assert code == 0
    env = json.loads(out.read_text())
    assert env["OPENAI_API_KEY"] == "sk-passed" and "ANTHROPIC_API_KEY" not in env
    err = capfd.readouterr().err
    assert err.count("burn: OPENAI_API_KEY passes through to the agent; a burn cannot revoke it") == 1
    assert "sk-passed" not in err


def test_allow_env_of_an_unset_variable_says_so(stack, capfd):
    out = stack.tmp / "env.json"
    assert instance(stack, py(DUMP_ENV, str(out)), allow_env=("NOT_SET_ANYWHERE",)).run() == 0
    assert "NOT_SET_ANYWHERE" not in json.loads(out.read_text())
    err = capfd.readouterr().err
    assert "NOT_SET_ANYWHERE is not set" in err and "NOT_SET_ANYWHERE passes through" not in err


@pytest.mark.parametrize("name", ["BURN_JOIN_TOKEN", "BURN_LOCAL_TOKEN", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE",
                                  "HOME", "PATH", "XDG_CONFIG_HOME", "HTTPS_PROXY", "no_proxy"])
def test_allow_env_refuses_names_burn_run_sets_itself(stack, capsys, name):
    with pytest.raises(SystemExit) as exited:
        cli(["run", "--role", "worker", "--allow-env", name, "--", "true"])
    assert exited.value.code == 2
    assert f"burn run sets {name} itself" in capsys.readouterr().err
    assert not stack.calls("POST", "/instances")  # refused before joining


@pytest.mark.parametrize("name", ["", "1ABC", "A-B", "A=B"])
def test_allow_env_refuses_what_is_not_a_variable_name(stack, capsys, name):
    with pytest.raises(SystemExit):
        cli(["run", "--role", "worker", "--allow-env", name, "--", "true"])
    assert "is not an environment variable name" in capsys.readouterr().err


def test_allow_env_never_carries_a_session_token(stack):
    out = stack.tmp / "env.json"
    inst = instance(stack, py(DUMP_ENV, str(out)), allow_env=("SNEAKY",))
    inst.host_env["SNEAKY"] = "bst_" + "a" * 43
    assert inst.run() == 0
    assert "SNEAKY" not in json.loads(out.read_text())


def test_a_child_instance_does_not_inherit_pass_throughs(stack, monkeypatch):
    from test_run import FakePopen
    import burn_cli.runner.run as runmod
    monkeypatch.setattr(runmod.subprocess, "Popen", FakePopen)
    inst = instance(stack, ["true"], allow_env=("OPENAI_API_KEY",))
    parent = stack.add_instance("sess_parent", "worker")
    inst.session = {"session_id": "sess_parent", "agent_id": parent["agent_id"]}
    inst._token = stack.session_tokens["sess_parent"]
    FakePopen.calls.clear()
    inst.spawn_child("worker", ["true"])
    argv, _ = FakePopen.calls[0]
    assert "--allow-env" not in argv
    inst._stop_children()
