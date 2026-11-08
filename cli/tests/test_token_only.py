"""An instance authenticates as itself: no admin token anywhere on a worker host, and its session token stays put."""
import json

import pytest

from burn_cli.api import ApiError
from burn_cli.runner.env import SESSION_TOKEN, child_env, scrub
from burn_cli.runner.credentials import from_secrets
from test_run import API_TOKEN, DUMP_ENV, HOST_SECRETS, host_env, instance, py, stack  # noqa: F401  (fixture)


def header(call, name):
    return next((v for k, v in call[3].items() if k.lower() == name), None)


def test_no_supervisor_request_carries_the_admin_token_or_the_join_token_after_the_join(stack):
    assert instance(stack, py("import time; time.sleep(0.6)"), heartbeat_every=0.1).run() == 0
    calls = [r for r in stack.requests if r[0] == "POST"]
    assert {r[1] for r in calls} >= {"/instances", f"/instances/{next(iter(stack.instances))}/heartbeat",
                                     f"/instances/{next(iter(stack.instances))}/exit"}
    for method, path, body, headers in stack.requests:
        assert "x-burn-demo-token" not in {k.lower() for k in headers}, f"{method} {path} sent the admin token"
    join = stack.calls("POST", "/instances")[0]
    assert join[2] == {"role": "worker", "enroll_token": stack.token} and "authorization" not in {k.lower() for k in join[3]}
    for method, path, body, headers in stack.requests[1:]:
        assert stack.token not in json.dumps(body) + json.dumps(headers)  # the join token is spent at the join


def test_heartbeat_and_exit_carry_this_instances_own_session_token(stack):
    assert instance(stack, py("import time; time.sleep(0.5)"), heartbeat_every=0.1).run() == 0
    sid = next(iter(stack.instances))
    for suffix in ("heartbeat", "exit"):
        (call, *_) = stack.calls("POST", f"/instances/{sid}/{suffix}")
        assert header(call, "authorization") == "Bearer " + stack.session_tokens[sid]


def test_the_admin_token_is_not_in_the_agents_environment_or_the_supervisors(stack, tmp_path):
    out = tmp_path / "env.json"
    inst = instance(stack, py(DUMP_ENV, str(out)))
    assert "BURN_DEMO_TOKEN" in HOST_SECRETS and "BURN_DEMO_TOKEN" not in inst.host_env  # dropped on construction
    assert inst.run() == 0
    assert API_TOKEN not in json.dumps(json.loads(out.read_text()))


def test_no_bst_value_reaches_the_agent_environment(stack, tmp_path):
    out = tmp_path / "env.json"
    assert instance(stack, py(DUMP_ENV, str(out))).run() == 0
    env = json.dumps(json.loads(out.read_text()))
    assert "bst_" not in env
    assert not [t for t in stack.session_tokens.values() if t in env]


def test_env_refuses_a_session_token_by_pattern_wherever_it_is():
    token = "bst_" + "A" * 43
    assert SESSION_TOKEN.search(f"prefix {token} suffix")
    assert scrub({"PATH": f"/bin:{token}", "LANG": "C", "TZ": token}) == {"LANG": "C"}  # allowed names, token inside
    creds = from_secrets({"badge": {"client_id": "c", "client_secret": "s", "token_url": "u"}, "session_token": token})
    assert token not in repr(creds) and not hasattr(creds, "session_token")
    with pytest.raises(ValueError, match="refusing to give the agent a session token"):
        child_env({}, session={"session_id": "s", "agent_id": "agent-a", "role": token}, credentials=creds,
                  mcp_url="u", spawn_url="v")


def test_a_spawn_with_the_parents_token_works_and_a_siblings_fails(stack):
    stack.roles["worker"]["may_spawn"] = ["worker"]
    parent, sibling = stack.add_instance("sess_p", "worker"), stack.add_instance("sess_s", "worker")
    inst = instance(stack, py("pass"))
    inst.session, inst._token = {"session_id": "sess_p", "agent_id": parent["agent_id"]}, stack.session_tokens["sess_p"]
    inst._children = []
    from unittest import mock
    with mock.patch("burn_cli.runner.run.subprocess.Popen") as popen:
        started = inst.spawn_child("worker", ["true"])
    assert started["session_id"] in stack.instances and popen.called
    call = stack.calls("POST", "/instances")[-1]
    assert call[2] == {"role": "worker", "parent_session": "sess_p"}  # exactly those two keys
    assert header(call, "authorization") == "Bearer " + stack.session_tokens["sess_p"]
    inst._token = stack.session_tokens["sess_s"]  # a sibling's token for the parent's session
    with pytest.raises(ApiError) as exc:
        inst.spawn_child("worker", ["true"])
    assert exc.value.status == 401


def test_a_burned_instances_heartbeat_gets_409_and_stops_it(stack):
    import time
    inst = instance(stack, py("import time; time.sleep(30)"), heartbeat_every=0.1, stop_grace=1)
    original = stack.handle

    def burn_after_first_beat(method, path, query, body):
        if path.endswith("/heartbeat"):
            for row in stack.instances.values():
                row["status"] = "revoked"
        return original(method, path, query, body)

    stack.handle = burn_after_first_beat
    stack.heartbeat_status = 409
    started = time.monotonic()
    from burn_cli.runner.run import EXIT_STOPPED
    assert inst.run() == EXIT_STOPPED and time.monotonic() - started < 15
