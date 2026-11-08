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


# --- session tokens ----------------------------------------------------------------------------

def test_each_instance_gets_its_own_session_token_and_only_its_hash_is_stored(env):
    import hashlib
    from burn_contracts import names
    from test_swarm import join, session_row
    make_role(env, WORKER)
    first, second = join(env, "worker")[1], join(env, "worker")[1]
    for joined in (first, second):
        token = joined["secrets"]["session_token"]
        assert names.is_session_token(token)
        row = session_row(env, joined["session_id"])
        assert row["session_token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in str(row)
    assert first["secrets"]["session_token"] != second["secrets"]["session_token"]
    listed = str(call(env, "GET", "/instances")[1]) + str(call(env, "GET", "/agents")[1])
    assert "session_token" not in listed and first["secrets"]["session_token"] not in listed


def test_a_scenario_session_gets_no_session_token(env):
    status, body = call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    assert status == 201 and "secrets" not in body


# --- an instance's own calls with its session token ---------------------------------------------

HELPER_ROLE = {"name": "helper", "tools": ["notify"], "credentials": ["api_key"]}


def as_instance(env, method, path, bearer, body=None, scheme="Bearer"):
    """A request with no admin token, only an Authorization header."""
    import json
    import handler
    event = {"requestContext": {"http": {"method": method, "path": path}},
             "headers": {"Authorization": f"{scheme} {bearer}"},
             "queryStringParameters": None, "body": None if body is None else json.dumps(body)}
    out = handler.handler(event)
    return out["statusCode"], json.loads(out["body"])


def _two_instances(env):
    from test_swarm import join
    make_role(env, WORKER | {"may_spawn": ["helper"]}); make_role(env, HELPER_ROLE)
    return join(env, "worker")[1], join(env, "worker")[1]


def test_an_instance_heartbeats_spawns_and_exits_with_its_session_token(env):
    a, _ = _two_instances(env)
    token = a["secrets"]["session_token"]
    assert as_instance(env, "POST", f"/instances/{a['session_id']}/heartbeat", token)[0] == 200
    status, child = as_instance(env, "POST", "/sessions", token, {"role": "helper", "parent_session": a["session_id"]})
    assert status == 201 and child["parent_session"] == a["session_id"]
    child_token = child["secrets"]["session_token"]
    assert as_instance(env, "POST", f"/instances/{child['session_id']}/heartbeat", child_token)[0] == 200
    assert as_instance(env, "POST", f"/instances/{a['session_id']}/exit", token)[0] == 200


def test_a_session_token_speaks_only_for_its_own_instance(env):
    a, b = _two_instances(env)
    token_a = a["secrets"]["session_token"]
    assert as_instance(env, "POST", f"/instances/{b['session_id']}/heartbeat", token_a)[0] == 401
    assert as_instance(env, "POST", f"/instances/{b['session_id']}/exit", token_a)[0] == 401
    assert as_instance(env, "POST", "/sessions", token_a, {"role": "helper", "parent_session": b["session_id"]})[0] == 401


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/instances", None), ("GET", "/events", None), ("GET", "/roles", None),
    ("POST", "/burns", {"scope": "instance", "id": "SELF"}), ("POST", "/roles/worker/tokens", {"uses": 1}),
    ("POST", "/sessions", {"agent_id": "agent-a"}),
    ("POST", "/sessions", {"role": "helper", "parent_session": "SELF", "extra": 1}),
    ("GET", "/instances/SELF/heartbeat", None),
])
def test_a_session_token_opens_nothing_else(env, method, path, body):
    a, _ = _two_instances(env)
    path = path.replace("SELF", a["session_id"])
    body = None if body is None else {k: (a["session_id"] if v == "SELF" else v) for k, v in body.items()}
    assert as_instance(env, method, path, a["secrets"]["session_token"], body)[0] == 401


def test_wrong_shapes_are_refused_without_a_lookup(env, monkeypatch):
    a, _ = _two_instances(env)
    token, heartbeat = a["secrets"]["session_token"], f"/instances/{a['session_id']}/heartbeat"
    import auth
    monkeypatch.setattr(auth.db, "table", lambda name: pytest.fail("a malformed request must cost no lookup"))
    for bearer, scheme in [(token, "Basic"), (token + "\n", "Bearer"), (token[:-1], "Bearer"), ("", "Bearer"),
                           (a["secrets"]["badge"]["client_secret"], "Bearer")]:
        assert as_instance(env, "POST", heartbeat, bearer, scheme=scheme)[0] == 401


def test_a_burned_instance_is_still_told_it_is_burned_and_cannot_spawn(env):
    a, _ = _two_instances(env)
    token = a["secrets"]["session_token"]
    assert call(env, "POST", "/burns", {"scope": "instance", "id": a["session_id"], "reason": "test"})[0] == 200
    # auth accepts the token; the routes refuse because the instance is burned
    assert as_instance(env, "POST", f"/instances/{a['session_id']}/heartbeat", token)[0] == 409
    assert as_instance(env, "POST", "/sessions", token, {"role": "helper", "parent_session": a["session_id"]})[0] == 403


def test_a_scenario_session_has_no_session_token_to_match(env):
    status, body = call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    import secrets as pysecrets
    from burn_contracts import names
    guess = names.SESSION_TOKEN_PREFIX + pysecrets.token_urlsafe(32)
    assert as_instance(env, "POST", f"/instances/{body['session_id']}/exit", guess)[0] == 401
