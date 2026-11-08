"""Which credential each route accepts: the admin token everywhere, a join token alone on a plain join."""
import pytest
from conftest import call
from test_swarm import WORKER, make_role

NO_ADMIN = ""  # call() then sends an empty x-burn-demo-token header
WELL_FORMED = "bjt_" + "0" * 16 + "." + "A" * 43  # right shape, never issued


def _join_token(env, role="worker"):
    return call(env, "POST", f"/roles/{role}/tokens", {"uses": 1, "ttl": 3600})[1]["token"]


def test_a_join_needs_only_the_join_token(env, monkeypatch):
    make_role(env, WORKER)
    token = _join_token(env)
    import auth
    monkeypatch.setattr(auth, "_expected", lambda: pytest.fail("a join must not need the admin token"))
    status, body = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token}, token=NO_ADMIN)
    assert status == 201 and body["agent_id"].startswith("agent-worker-")


def test_the_route_still_checks_the_join_token(env):
    make_role(env, WORKER)
    token = _join_token(env)
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": WELL_FORMED}, token=NO_ADMIN)[0] == 403
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token}, token=NO_ADMIN)[0] == 201
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token}, token=NO_ADMIN)[0] == 403  # used up


@pytest.mark.parametrize("body", [
    {"role": "worker", "enroll_token": "not-a-join-token"},
    {"role": "worker", "enroll_token": WELL_FORMED + "\n"},
    {"role": "worker", "enroll_token": WELL_FORMED, "parent_session": "sess_0123456789abcdef"},
    {"role": "worker", "enroll_token": WELL_FORMED, "agent_id": "agent-a"},
    {"role": "worker", "enroll_token": WELL_FORMED, "extra": 1},
    {"role": ["worker"], "enroll_token": WELL_FORMED},
    {"role": "worker", "enroll_token": 5},
    {"role": "worker", "parent_session": "sess_0123456789abcdef"},
    {"agent_id": "agent-a"},
    {},
])
def test_any_other_body_on_the_join_route_needs_the_admin_token(env, body):
    make_role(env, WORKER)
    assert call(env, "POST", "/sessions", body, token=NO_ADMIN)[0] == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/events"), ("GET", "/roles"), ("POST", "/roles/worker/tokens"), ("GET", "/instances"),
    ("POST", "/burns"), ("POST", "/sessions/"), ("PUT", "/roles/worker"),
])
def test_a_join_body_opens_no_other_route(env, method, path):
    make_role(env, WORKER)
    body = {"role": "worker", "enroll_token": _join_token(env)}
    assert call(env, method, path, body, token=NO_ADMIN)[0] == 401


def test_the_admin_token_still_opens_every_route(env):
    make_role(env, WORKER)
    assert call(env, "GET", "/roles")[0] == 200
    assert call(env, "GET", "/roles", token="wrong")[0] == 401
