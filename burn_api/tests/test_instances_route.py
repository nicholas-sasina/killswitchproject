"""POST /instances: the two role forms of POST /sessions on a path of their own."""
import pytest
from conftest import call
from test_auth import HELPER_ROLE, NO_ADMIN, WELL_FORMED, as_instance
from test_swarm import WORKER, join, make_role, session_row

SPAWNER = WORKER | {"may_spawn": ["helper"]}


def _token(env, role="worker", uses=1):
    return call(env, "POST", f"/roles/{role}/tokens", {"uses": uses, "ttl": 3600})[1]["token"]


def _two_instances(env):
    make_role(env, SPAWNER); make_role(env, HELPER_ROLE)
    return join(env, "worker")[1], join(env, "worker")[1]


def test_a_join_on_instances_needs_no_admin_header(env, monkeypatch):
    make_role(env, WORKER)
    token = _token(env)
    import auth
    monkeypatch.setattr(auth, "_expected", lambda: pytest.fail("a join must not need the admin token"))
    status, body = call(env, "POST", "/instances", {"role": "worker", "enroll_token": token}, token=NO_ADMIN)
    assert status == 201 and body["agent_id"].startswith("agent-worker-")
    assert set(body["secrets"]) >= {"badge", "session_token", "api_key", "aws_key"}
    assert session_row(env, body["session_id"])["token_id"]


def test_the_join_token_is_still_checked_on_instances(env):
    make_role(env, WORKER)
    token = _token(env)
    assert call(env, "POST", "/instances", {"role": "worker", "enroll_token": WELL_FORMED}, token=NO_ADMIN)[0] == 403
    assert call(env, "POST", "/instances", {"role": "worker", "enroll_token": token}, token=NO_ADMIN)[0] == 201
    assert call(env, "POST", "/instances", {"role": "worker", "enroll_token": token}, token=NO_ADMIN)[0] == 403
    assert call(env, "POST", "/instances", {"role": "ghost", "enroll_token": token}, token=NO_ADMIN)[0] == 404


def test_a_spawn_on_instances_works_with_the_parents_bearer(env):
    parent, _ = _two_instances(env)
    status, child = as_instance(env, "POST", "/instances", parent["secrets"]["session_token"],
                                {"role": "helper", "parent_session": parent["session_id"]})
    assert status == 201 and child["parent_session"] == parent["session_id"]
    assert child["agent_id"].startswith("agent-helper-") and child["secrets"]["session_token"]
    # the narrowed child, same as through /sessions
    assert child["tools"] == ["notify"] and "aws_key" not in child["secrets"]


def test_a_spawn_with_another_instances_token_is_401(env):
    a, b = _two_instances(env)
    body = {"role": "helper", "parent_session": b["session_id"]}
    assert as_instance(env, "POST", "/instances", a["secrets"]["session_token"], body)[0] == 401
    assert as_instance(env, "POST", "/instances", "bst_" + "A" * 43, body)[0] == 401
    assert call(env, "POST", "/instances", body, token=NO_ADMIN)[0] == 401
    assert [r for r in env.ddb.Table("burn-sessions").scan()["Items"] if r.get("parent_session")] == []


def test_a_spawn_is_still_refused_when_the_parent_may_not_spawn_that_role(env):
    parent, _ = _two_instances(env)
    status, _ = as_instance(env, "POST", "/instances", parent["secrets"]["session_token"],
                            {"role": "worker", "parent_session": parent["session_id"]})
    assert status == 403


@pytest.mark.parametrize("body", [
    {"role": "worker", "enroll_token": "not-a-join-token"},
    {"role": "worker", "enroll_token": WELL_FORMED + "\n"},
    {"role": "worker", "enroll_token": WELL_FORMED, "parent_session": "sess_0123456789abcdef"},  # mixed
    {"role": "worker", "enroll_token": WELL_FORMED, "agent_id": "agent-a"},
    {"role": "worker", "enroll_token": WELL_FORMED, "extra": 1},                                  # extra key
    {"role": ["worker"], "enroll_token": WELL_FORMED},
    {"role": "worker", "enroll_token": 5},
    {"role": "worker"},
    {"agent_id": "agent-a"},
    {},
])
def test_mixed_extra_or_malformed_bodies_get_no_exemption(env, body):
    make_role(env, WORKER)
    assert call(env, "POST", "/instances", body, token=NO_ADMIN)[0] == 401
    assert env.ddb.Table("burn-sessions").scan()["Items"] == []


def test_the_admin_token_may_start_either_form_and_gets_400_for_anything_else(env):
    parent, _ = _two_instances(env)
    assert call(env, "POST", "/instances", {"role": "worker", "enroll_token": _token(env)})[0] == 201
    assert call(env, "POST", "/instances", {"role": "helper", "parent_session": parent["session_id"]})[0] == 201
    for bad in ({"agent_id": "agent-a"}, {}, {"role": "worker"}, {"role": ["worker"], "enroll_token": WELL_FORMED},
                {"role": "worker", "enroll_token": WELL_FORMED, "parent_session": parent["session_id"]},
                {"role": "worker", "enroll_token": 5}, {"role": "helper", "parent_session": ["x"]}):
        assert call(env, "POST", "/instances", bad)[0] == 400, bad
    assert call(env, "POST", "/sessions", {"agent_id": "agent-a"})[0] == 201  # the scenario form stays here


def test_instances_with_a_join_body_opens_no_other_route(env):
    make_role(env, WORKER)
    body = {"role": "worker", "enroll_token": _token(env)}
    for method, path in (("GET", "/instances"), ("POST", "/instances/sess_0123456789abcdef/exit"),
                         ("POST", "/instances/sess_0123456789abcdef/heartbeat"), ("PUT", "/instances"),
                         ("POST", "/instances/"), ("POST", "/burns")):
        assert call(env, method, path, body, token=NO_ADMIN)[0] == 401, (method, path)


def test_heartbeat_and_exit_are_unaffected(env):
    a, b = _two_instances(env)
    token = a["secrets"]["session_token"]
    assert as_instance(env, "POST", f"/instances/{a['session_id']}/heartbeat", token)[0] == 200
    assert as_instance(env, "POST", f"/instances/{b['session_id']}/heartbeat", token)[0] == 401  # not its own
    assert as_instance(env, "POST", f"/instances/{b['session_id']}/exit", token)[0] == 401
    assert call(env, "POST", f"/instances/{b['session_id']}/heartbeat")[0] == 200  # admin still may
    assert as_instance(env, "POST", f"/instances/{a['session_id']}/exit", token)[0] == 200
    assert call(env, "POST", f"/instances/{b['session_id']}/exit")[0] == 200


def test_the_role_forms_of_post_sessions_are_unchanged(env):
    make_role(env, SPAWNER); make_role(env, HELPER_ROLE)
    joined = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": _token(env)}, token=NO_ADMIN)
    assert joined[0] == 201
    child = as_instance(env, "POST", "/sessions", joined[1]["secrets"]["session_token"],
                        {"role": "helper", "parent_session": joined[1]["session_id"]})
    assert child[0] == 201 and child[1]["parent_session"] == joined[1]["session_id"]
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": "bad"}, token=NO_ADMIN)[0] == 401
