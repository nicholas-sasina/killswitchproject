"""People sign-in in the API: both modes, the exempt routes, and who-did-what stamping."""
import base64
import json

import pytest
from conftest import call
from test_swarm import HELPER, WORKER
from test_swarm import make_role as admin_role

PERSON = "ada@example.com"


def request(env, method, path, body=None, claims=None, headers=None, admin=False):
    """A request as API Gateway would hand it over; claims are what its JWT authorizer verified."""
    import handler
    context = {"http": {"method": method, "path": path}}
    if claims is not None:
        context["authorizer"] = {"jwt": {"claims": claims, "scopes": None}}
    hdrs = dict(headers or {})
    if admin:
        hdrs["x-burn-demo-token"] = env.token
    event = {"requestContext": context, "headers": hdrs, "queryStringParameters": None,
             "body": None if body is None else json.dumps(body)}
    out = handler.handler(event)
    return out["statusCode"], json.loads(out["body"]) if out["body"] else None


def as_person(env, method, path, body=None, email=PERSON):
    return request(env, method, path, body, claims={"sub": "0000-1111", "email": email, "token_use": "id"})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def signin_on(monkeypatch):
    monkeypatch.setenv("BURN_PEOPLE_AUTH", "1")
    monkeypatch.delenv("BURN_LOCAL_DEV", raising=False)


def make_role(env, spec):
    """Create a role as whoever may: a person once sign-in is on, the admin token before."""
    import auth
    status, body = as_person(env, "POST", "/roles", spec) if auth.people_auth_on() else (None, admin_role(env, spec))
    assert status in (None, 201), body
    return body


def events(env):
    return env.ddb.Table("burn-events").scan()["Items"]


def token_for(env, role="worker", uses=1, **who):
    status, body = (as_person(env, "POST", f"/roles/{role}/tokens", {"uses": uses}) if who.get("person")
                    else request(env, "POST", f"/roles/{role}/tokens", {"uses": uses}, admin=True))
    assert status == 201, body
    return body


# --- sign-in off: exactly as before ------------------------------------------------------------

def test_off_the_admin_token_works_everywhere_as_before(env):
    assert request(env, "GET", "/roles", admin=True)[0] == 200
    assert request(env, "POST", "/sessions", {"agent_id": "agent-a"}, admin=True)[0] == 201
    assert request(env, "GET", "/roles")[0] == 401


def test_off_joins_and_spawns_work_on_both_paths_as_before(env):
    make_role(env, WORKER | {"may_spawn": ["helper"]}); make_role(env, HELPER)
    for path in ("/sessions", "/instances"):
        status, joined = request(env, "POST", path, {"role": "worker", "enroll_token": token_for(env)["token"]})
        assert status == 201, path
        spawn = {"role": "helper", "parent_session": joined["session_id"]}
        assert request(env, "POST", path, spawn, headers=bearer(joined["secrets"]["session_token"]))[0] == 201


def test_off_an_admin_caller_is_actor_admin_with_no_created_by(env):
    make_role(env, WORKER)
    token = token_for(env)
    assert token["created_by"] is None
    request(env, "POST", "/sessions", {"agent_id": "agent-a"}, admin=True)
    issued = [e for e in events(env) if e["action"] == "issued"]
    assert issued and all(e["actor"] == "admin" for e in issued)


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/roles", None), ("POST", "/sessions", {"agent_id": "agent-a"}),
    ("POST", "/burn", {"agent_id": "agent-a"}), ("POST", "/roles/worker/tokens", {"uses": 1}),
])
def test_off_forged_claims_without_the_admin_token_are_401(env, method, path, body):
    """With sign-in off no authorizer runs, so claims can only come from a forged event (a direct invoke)."""
    make_role(env, WORKER)
    assert as_person(env, method, path, body)[0] == 401


def test_off_claims_never_become_a_person_even_beside_the_admin_token(env):
    make_role(env, WORKER)
    status, token = request(env, "POST", "/roles/worker/tokens", {"uses": 1}, admin=True,
                            claims={"sub": "x", "email": PERSON})
    assert status == 201 and token["created_by"] is None  # the caller is the admin, not the claimed person


# --- sign-in on: a person on every route but the exempt ones -----------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/roles", None), ("GET", "/events", None), ("GET", "/instances", None),
    ("POST", "/sessions", {"agent_id": "agent-a"}), ("POST", "/burn", {"agent_id": "agent-a"}),
    ("POST", "/roles", {"name": "x", "tools": []}), ("POST", "/burns", {"scope": "role", "id": "a"}),
])
def test_on_no_claims_is_401_even_with_the_admin_token(env, signin_on, method, path, body):
    assert request(env, method, path, body)[0] == 401
    assert request(env, method, path, body, admin=True)[0] == 401


def test_on_a_person_may_call_them(env, signin_on):
    assert as_person(env, "GET", "/roles")[0] == 200
    assert as_person(env, "POST", "/sessions", {"agent_id": "agent-a"})[0] == 201


def test_on_the_admin_token_works_only_in_local_development(env, signin_on, monkeypatch):
    monkeypatch.setenv("BURN_LOCAL_DEV", "1")
    assert request(env, "GET", "/roles", admin=True)[0] == 200
    monkeypatch.setenv("BURN_LOCAL_DEV", "0")
    assert request(env, "GET", "/roles", admin=True)[0] == 401


@pytest.mark.parametrize("claims", [
    {}, {"sub": "x"}, {"email": PERSON}, {"sub": "x", "email": "not-an-email"}, {"sub": "x", "email": 5}, "a@b.c",
])
def test_on_claims_without_a_person_are_401(env, signin_on, claims):
    assert request(env, "GET", "/roles", claims=claims)[0] == 401


def test_on_claims_count_only_from_request_context(env, signin_on):
    # An unsigned, made-up token: it proves nothing, so it must never be taken for a person.
    fake_jwt = ".".join(base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")
                        for part in ({"alg": "none"}, {"sub": "x", "email": PERSON})) + "."
    forged = {"Authorization": f"Bearer {fake_jwt}", "x-amzn-oidc-data": fake_jwt,
              "requestContext": json.dumps({"authorizer": {"jwt": {"claims": {"sub": "x", "email": PERSON}}}})}
    assert request(env, "GET", "/roles", headers=forged)[0] == 401
    body = {"name": "x", "tools": [], "claims": {"sub": "x", "email": PERSON},
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "x", "email": PERSON}}}}}
    assert request(env, "POST", "/roles", body)[0] == 401


def test_on_the_exempt_instance_routes_take_their_own_credential(env, signin_on):
    make_role(env, WORKER | {"may_spawn": ["helper"]}); make_role(env, HELPER)
    status, joined = request(env, "POST", "/instances", {"role": "worker",
                                                         "enroll_token": token_for(env, person=True)["token"]})
    assert status == 201
    own = bearer(joined["secrets"]["session_token"])
    sid = joined["session_id"]
    assert request(env, "POST", f"/instances/{sid}/heartbeat", headers=own)[0] == 200
    spawned = request(env, "POST", "/instances", {"role": "helper", "parent_session": sid}, headers=own)
    assert spawned[0] == 201
    assert request(env, "POST", f"/instances/{sid}/exit", headers=own)[0] == 200


def test_on_an_admin_header_cannot_open_an_instance_route(env, signin_on):
    make_role(env, WORKER)
    joined = request(env, "POST", "/instances", {"role": "worker",
                                                 "enroll_token": token_for(env, person=True)["token"]})[1]
    sid = joined["session_id"]
    assert request(env, "POST", f"/instances/{sid}/exit", admin=True)[0] == 401
    assert request(env, "POST", f"/instances/{sid}/heartbeat", admin=True)[0] == 401
    assert request(env, "POST", "/instances", {"role": "worker", "enroll_token": "bjt_x"}, admin=True)[0] == 401
    from test_swarm import session_row
    assert session_row(env, sid)["status"] == "active"


def test_on_post_sessions_needs_a_person_for_every_form(env, signin_on):
    make_role(env, WORKER | {"may_spawn": ["helper"]}); make_role(env, HELPER)
    token = token_for(env, uses=2, person=True)["token"]
    assert request(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 401
    joined = request(env, "POST", "/instances", {"role": "worker", "enroll_token": token})[1]
    spawn = {"role": "helper", "parent_session": joined["session_id"]}
    assert request(env, "POST", "/sessions", spawn, headers=bearer(joined["secrets"]["session_token"]))[0] == 401
    assert as_person(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 201


def test_on_a_preflight_and_a_canary_incident_need_no_person(env, signin_on, monkeypatch):
    import canary
    import handler
    preflight = handler.handler({"requestContext": {"http": {"method": "OPTIONS", "path": "/roles"}},
                                 "headers": {"origin": "http://localhost:5173"}})
    assert preflight["statusCode"] == 204
    seen = []
    monkeypatch.setattr(canary, "handle_incident", lambda incident: seen.append(incident) or {"ok": True})
    alias = type("Context", (), {"invoked_function_arn":
                                 "arn:aws:lambda:us-east-1:123456789012:function:burn-api:canary"})()
    assert handler.handler({"canary_incident": {"secret_ref": "burn/agent-a/canary_x"}}, alias) == {"ok": True}
    assert seen == [{"secret_ref": "burn/agent-a/canary_x"}]


# --- who did what ------------------------------------------------------------------------------

def test_a_person_is_created_by_on_their_token_and_on_the_instances_that_join_with_it(env, signin_on):
    make_role(env, WORKER | {"may_spawn": ["helper"]}); make_role(env, HELPER)
    token = token_for(env, person=True)
    assert token["created_by"] == PERSON
    row = env.ddb.Table("burn-enroll-tokens").get_item(Key={"token_id": token["token_id"]})["Item"]
    assert row["created_by"] == PERSON
    joined = request(env, "POST", "/instances", {"role": "worker", "enroll_token": token["token"]})[1]
    assert joined["created_by"] == PERSON
    child = request(env, "POST", "/instances", {"role": "helper", "parent_session": joined["session_id"]},
                    headers=bearer(joined["secrets"]["session_token"]))[1]
    assert child["created_by"] == PERSON  # inherited through the parent
    listed = {i["session_id"]: i for i in as_person(env, "GET", "/instances")[1]["instances"]}
    assert listed[joined["session_id"]]["created_by"] == listed[child["session_id"]]["created_by"] == PERSON


def test_actor_names_the_person_the_instance_or_the_new_instance(env, signin_on):
    make_role(env, WORKER | {"may_spawn": ["helper"]}); make_role(env, HELPER)
    token = token_for(env, person=True)
    joined = request(env, "POST", "/instances", {"role": "worker", "enroll_token": token["token"]})[1]
    sid = joined["session_id"]
    child = request(env, "POST", "/instances", {"role": "helper", "parent_session": sid},
                    headers=bearer(joined["secrets"]["session_token"]))[1]
    by_session = {}
    for e in events(env):
        by_session.setdefault(e.get("session_id"), set()).add(e.get("actor"))
    assert by_session[sid] == {f"instance:{sid}"}  # a join's own events are the new instance's
    assert by_session[child["session_id"]] == {f"instance:{sid}"}  # spawned by its parent
    as_person(env, "POST", "/burns", {"scope": "lineage", "id": sid})
    burned = [e for e in events(env) if e["source"] == "burn"]
    assert burned and all(e["actor"] == PERSON for e in burned)  # carried into the burn's worker threads


OLD_TOKEN_ID = "f" * 16  # a row written before sign-in existed


def test_older_rows_without_created_by_read_as_null(env):
    make_role(env, WORKER)
    env.ddb.Table("burn-enroll-tokens").put_item(Item={
        "token_id": OLD_TOKEN_ID, "role": "worker", "secret_sha256": "0" * 64, "uses": 1, "uses_left": 1,
        "revoked": False, "expires_at": "2099-01-01T00:00:00.000Z", "created_at": "2026-01-01T00:00:00.000Z"})
    assert request(env, "DELETE", f"/tokens/{OLD_TOKEN_ID}", admin=True)[1]["created_by"] is None
