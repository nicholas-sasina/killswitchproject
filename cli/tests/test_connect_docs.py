"""The README's "Connect your agent" names only variables that burn run really gives the agent.

The variables are compared with the environment of an agent that a real Instance started, so a rename in env.py
or run.py that the README misses fails here.
"""
import json
import re
from pathlib import Path

import pytest

from burn_cli.runner.instance_api import InstanceApi
from burn_cli.runner.run import Instance
from test_run import DUMP_ENV, host_env, py, stack  # noqa: F401 - stack is a fixture

README = (Path(__file__).resolve().parents[1] / "README.md").read_text()
BURN_NAME = re.compile(r"\bBURN_[A-Z_]+\b")
AWS_NAME = re.compile(r"\bAWS_[A-Z_]+\b")


def section(heading: str) -> str:
    """The README's text under `heading`, up to the next heading of the same or a higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    start = README.index(f"\n{heading}\n") + len(heading) + 2
    end = re.compile(rf"^#{{1,{level}}} ", re.M).search(README, start)
    return README[start:end.start() if end else len(README)]


def table_rows(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if line.startswith("|"))


def code_blocks(text: str) -> str:
    return "\n".join(re.findall(r"^```[a-z]*\n(.*?)^```", text, re.S | re.M))


@pytest.fixture
def agent_env(stack):
    """What a real burn run hands its agent: a role with api_key and aws_key, and a launcher's task set."""
    out = stack.tmp / "env.json"
    env = host_env(stack, BURN_TASK='["item-01"]', BURN_RESUME='{"done": [], "quarantined": []}')
    inst = Instance(InstanceApi(stack.url), "worker", py(DUMP_ENV, str(out)), gateway_url=stack.gw.url + "/mcp",
                    host_env=env, enroll_token=stack.token, sleep=lambda _: None, heartbeat_every=30.0)
    assert inst.run() == 0
    return json.loads(out.read_text())


def test_the_agent_table_lists_exactly_the_burn_variables_burn_run_sets(agent_env):
    table = table_rows(section("### What your agent gets"))
    assert set(BURN_NAME.findall(table)) == {name for name in agent_env if name.startswith("BURN_")}
    assert set(AWS_NAME.findall(table)) <= set(agent_env)


def test_every_variable_the_harness_snippets_use_is_one_burn_run_sets(agent_env):
    used = set(BURN_NAME.findall(code_blocks(section("### Connect your harness"))))
    assert {"BURN_MCP_URL", "BURN_LOCAL_TOKEN"} <= used
    assert used <= set(agent_env), sorted(used - set(agent_env))


def test_the_sections_are_where_the_tests_look():
    assert "\n## Connect your agent\n" in README  # the dashboard's join commands link to #connect-your-agent
    assert "Authorization: Bearer $BURN_LOCAL_TOKEN" in section("### Connect your harness")
    assert section("### What your agent gets").count("\n|") >= 7
