"""Roles, join tokens, instance sessions, heartbeat, exit and the idle sweep (moto + fake minter)."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from conftest import call

WORKER = {"name": "worker", "tools": ["read_ticket", "notify"], "credentials": ["api_key", "aws_key"],
          "may_spawn": ["helper"], "egress": ["a.example.com", "b.example.com"], "owner": "team-x"}
HELPER = {"name": "helper", "tools": ["notify", "send_email"], "credentials": ["api_key"],
          "egress": ["b.example.com", "c.example.com"]}


def make_role(env, spec, **kw):
    status, body = call(env, "POST", "/roles", spec | kw)
    assert status == 201, body
    return body


def join(env, role, uses=1, ttl=3600):
    token = call(env, "POST", f"/roles/{role}/tokens", {"uses": uses, "ttl": ttl})[1]
    status, body = call(env, "POST", "/sessions", {"role": role, "enroll_token": token["token"]})
    return status, body, token


def session_row(env, session_id):
    return env.ddb.Table("burn-sessions").get_item(Key={"session_id": session_id})["Item"]


# --- roles -------------------------------------------------------------------------------------

def test_role_create_get_and_conflict(env):
    role = make_role(env, WORKER)
    assert role["managed_by"] == "ui" and role["active_instances"] == 0 and role["tools"] == WORKER["tools"]
    assert call(env, "GET", "/roles/worker")[1]["owner"] == "team-x"
    assert call(env, "POST", "/roles", WORKER)[0] == 409
    assert call(env, "GET", "/roles/nope")[0] == 404


@pytest.mark.parametrize("bad", [
    {"name": "Bad Name", "tools": []}, {"name": "x", "tools": "notalist"}, {"name": "x"},
    {"name": "x", "tools": [], "credentials": ["root_key"]}, {"name": "x", "tools": [], "managed_by": "scenario"},
    {"name": "x", "tools": [], "egress": ["not a host!"]}, {"name": "x" * 33, "tools": []},
])
def test_role_validation(env, bad):
    assert call(env, "POST", "/roles", bad)[0] == 400


def test_put_creates_replaces_and_enforces_ownership(env):
    status, body = call(env, "PUT", "/roles/worker", WORKER | {"managed_by": "code"})
    assert status == 200 and body["managed_by"] == "code"
    created = body["created_at"]
    status, body = call(env, "PUT", "/roles/worker", WORKER | {"managed_by": "code", "tools": ["notify"]})
    assert status == 200 and body["tools"] == ["notify"] and body["created_at"] == created
    assert call(env, "PUT", "/roles/worker", WORKER | {"managed_by": "ui"})[0] == 409  # code-managed
    assert call(env, "PUT", "/roles/other", WORKER)[0] == 400  # body name must match the path
    assert call(env, "GET", "/roles/worker")[1]["tools"] == ["notify"]


def test_ui_role_cannot_be_taken_by_code_and_scenario_roles_are_read_only(env):
    make_role(env, HELPER)
    assert call(env, "PUT", "/roles/helper", HELPER | {"managed_by": "code"})[0] == 409
    # agent-a / agent-b are scenario agents: roles "a" and "b", never stored
    assert call(env, "PUT", "/roles/a", {"name": "a", "tools": []})[0] == 409
    assert call(env, "POST", "/roles", {"name": "b", "tools": []})[0] == 409
    assert [r["name"] for r in env.ddb.Table("burn-roles").scan()["Items"]] == ["helper"]


def test_list_roles_includes_scenario_and_active_instances(env):
    make_role(env, WORKER)
    call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    join(env, "worker", uses=2)
    roles = {r["name"]: r for r in call(env, "GET", "/roles")[1]["roles"]}
    assert set(roles) == {"a", "b", "worker"}
    assert roles["a"]["managed_by"] == "scenario" and roles["a"]["may_message"] == ["b"]
    assert roles["a"]["active_instances"] == 1 and roles["b"]["active_instances"] == 0
    assert roles["worker"]["active_instances"] == 1


# --- join tokens -------------------------------------------------------------------------------

def test_token_format_and_only_hash_stored(env):
    from burn_contracts import names
    import hashlib
    make_role(env, WORKER)
    status, token = call(env, "POST", "/roles/worker/tokens", {"uses": 3, "ttl": 120})
    assert status == 201 and token["status"] == "active" and token["uses_left"] == 3
    token_id, secret = names.parse_join_token(token["token"])
    assert token_id == token["token_id"]
    row = env.ddb.Table("burn-enroll-tokens").get_item(Key={"token_id": token_id})["Item"]
    assert row["secret_sha256"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in str(row) and "token" not in row
    assert call(env, "POST", "/roles/ghost/tokens", {})[0] == 404


@pytest.mark.parametrize("body", [{"uses": 0}, {"uses": 10001}, {"ttl": 59}, {"ttl": 2592001}, {"uses": "3"}])
def test_token_limits_validated(env, body):
    make_role(env, WORKER)
    assert call(env, "POST", "/roles/worker/tokens", body)[0] == 400


def test_join_with_token_uses_up_and_is_refused_afterwards(env):
    make_role(env, WORKER)
    status, body, token = join(env, "worker", uses=1)
    assert status == 201 and body["token_id"] == token["token_id"]
    again = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})
    assert again[0] == 403 and "used_up" in again[1]["error"]


def test_bad_tokens_are_403_and_never_echoed(env):
    make_role(env, WORKER); make_role(env, HELPER)
    token = call(env, "POST", "/roles/worker/tokens", {})[1]
    forged = token["token"][:-1] + ("A" if token["token"][-1] != "A" else "B")
    for bad in ["junk", forged, "bjt_" + "0" * 16 + "." + "A" * 43, 5]:
        status, body = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": bad})
        assert status == 403, bad
        assert str(bad) not in body["error"] or bad == 5
    status, body = call(env, "POST", "/sessions", {"role": "helper", "enroll_token": token["token"]})
    assert status == 403 and "different role" in body["error"] and token["token"] not in body["error"]
    assert call(env, "GET", "/instances")[1]["instances"] == []


def test_revoked_and_expired_tokens(env):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {})[1]
    status, body = call(env, "DELETE", f"/tokens/{token['token_id']}")
    assert status == 200 and body["status"] == "revoked" and "token" not in body
    status, body = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})
    assert status == 403 and "revoked" in body["error"]
    assert call(env, "DELETE", "/tokens/ffffffffffffffff")[0] == 404

    expired = call(env, "POST", "/roles/worker/tokens", {})[1]
    env.ddb.Table("burn-enroll-tokens").update_item(
        Key={"token_id": expired["token_id"]}, UpdateExpression="SET expires_at = :t",
        ExpressionAttributeValues={":t": "2020-01-01T00:00:00.000Z"})
    status, body = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": expired["token"]})
    assert status == 403 and "expired" in body["error"]


def test_parallel_joins_never_overuse_a_token(env):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 5})[1]
    barrier = threading.Barrier(20)

    def attempt(_):
        barrier.wait()
        return call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[0]

    with ThreadPoolExecutor(20) as pool:
        statuses = list(pool.map(attempt, range(20)))
    assert statuses.count(201) == 5 and statuses.count(403) == 15
    row = env.ddb.Table("burn-enroll-tokens").get_item(Key={"token_id": token["token_id"]})["Item"]
    assert row["uses_left"] == 0
    assert len(call(env, "GET", "/instances")[1]["instances"]) == 5


def test_failed_join_gives_the_use_back(env):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 1})[1]
    env.minter.fail = True
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[0] == 502
    env.minter.fail = False
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[0] == 201


# --- instance sessions -------------------------------------------------------------------------

def test_instance_session_row_and_events(env):
    from burn_contracts import names
    make_role(env, WORKER)
    status, body, token = join(env, "worker")
    assert status == 201
    assert body["agent_id"].startswith("agent-worker-") and names.is_agent_id(body["agent_id"])
    assert body["badge_ref"] == f"burn/{body['agent_id']}/badge"
    assert {c["type"] for c in body["credentials"]} == {"api_key", "aws_key"}
    assert body["tools"] == WORKER["tools"] and body["egress"] == WORKER["egress"]
    row = session_row(env, body["session_id"])
    assert row["status"] == "active" and row["role"] == "worker" and row["token_id"] == token["token_id"]
    assert row["tools"] == WORKER["tools"] and row["send_to"] == [] and row["owner"] == "team-x"
    assert row["parent_session"] is None and row["last_seen"] and row["exited_at"] is None
    events = call(env, "GET", "/events")[1]["events"]
    assert [(e["source"], e["action"]) for e in events] == [("session", "issued"), ("instance", "started")]
    assert events[1]["role"] == "worker" and events[1]["session_id"] == body["session_id"]
    assert token["token"] not in str(events)


def test_two_instances_get_their_own_agent_ids(env):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 2})[1]["token"]
    ids = {call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[1]["agent_id"]
           for _ in range(2)}
    assert len(ids) == 2


def test_session_request_forms(env):
    make_role(env, WORKER)
    assert call(env, "POST", "/sessions", {"role": "worker"})[0] == 400
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": "x", "parent_session": "y"})[0] == 400
    assert call(env, "POST", "/sessions", {"role": "Bad", "enroll_token": "x"})[0] == 400
    assert call(env, "POST", "/sessions", {"role": "ghost", "enroll_token": "x"})[0] == 404
    assert call(env, "POST", "/sessions", {"role": "a", "enroll_token": "x"})[0] == 403  # scenario role
    # an instance's agent id cannot be used to start a scenario-style session
    agent_id = join(env, "worker")[1]["agent_id"]
    assert call(env, "POST", "/sessions", {"agent_id": agent_id})[0] == 404


def test_scenario_session_is_unchanged(env):
    status, body = call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    assert status == 201 and set(body) == {"session_id", "agent_id", "badge_ref", "credentials"}
    row = session_row(env, body["session_id"])
    assert "role" not in row and "token_id" not in row and row["send_to"] == ["agent-b"]


# --- spawning ----------------------------------------------------------------------------------

def spawn(env, parent, role):
    return call(env, "POST", "/sessions", {"role": role, "parent_session": parent})


def test_spawn_narrows_tools_credentials_and_egress(env):
    make_role(env, WORKER); make_role(env, HELPER)
    parent = join(env, "worker")[1]
    status, child = spawn(env, parent["session_id"], "helper")
    assert status == 201
    assert child["tools"] == ["notify"]                       # helper tools ∩ worker tools
    assert [c["type"] for c in child["credentials"]] == ["api_key"]
    assert child["egress"] == ["b.example.com"]               # helper hosts ∩ worker hosts
    assert child["parent_session"] == parent["session_id"] and child["token_id"] is None
    row = session_row(env, child["session_id"])
    assert row["tools"] == ["notify"] and row["parent_session"] == parent["session_id"]
    started = [e for e in call(env, "GET", "/events")[1]["events"] if e["action"] == "started"][-1]
    assert started["parent_session"] == parent["session_id"] and started["role"] == "helper"


def test_spawn_null_egress_takes_the_other_side(env):
    make_role(env, WORKER | {"egress": None}); make_role(env, HELPER)
    child = spawn(env, join(env, "worker")[1]["session_id"], "helper")[1]
    assert child["egress"] == HELPER["egress"]
    make_role(env, {"name": "open", "tools": ["notify"], "egress": None})
    env.ddb.Table("burn-roles").update_item(Key={"name": "worker"}, UpdateExpression="SET may_spawn = :m",
                                            ExpressionAttributeValues={":m": ["helper", "open"]})
    parent = join(env, "worker")[1]  # worker role now has egress None; give it hosts via a new role
    assert spawn(env, parent["session_id"], "open")[1]["egress"] is None


def test_grandchild_cannot_exceed_its_parent(env):
    make_role(env, WORKER | {"may_spawn": ["helper"]})
    make_role(env, HELPER | {"may_spawn": ["leaf"]})
    make_role(env, {"name": "leaf", "tools": ["read_ticket", "notify", "send_email"],
                    "credentials": ["api_key", "aws_key"], "egress": ["a.example.com", "b.example.com"]})
    helper = spawn(env, join(env, "worker")[1]["session_id"], "helper")[1]
    leaf = spawn(env, helper["session_id"], "leaf")[1]
    assert leaf["tools"] == ["notify"] and [c["type"] for c in leaf["credentials"]] == ["api_key"]
    assert leaf["egress"] == ["b.example.com"]


def test_spawn_refusals(env):
    make_role(env, WORKER); make_role(env, HELPER)
    parent = join(env, "worker")[1]
    assert spawn(env, parent["session_id"], "worker")[0] == 403         # not in may_spawn
    assert spawn(env, "sess_unknown", "helper")[0] == 404
    assert spawn(env, parent["session_id"], "ghost")[0] == 404
    assert spawn(env, parent["session_id"], "a")[0] == 403              # scenario role
    scenario = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    assert spawn(env, scenario["session_id"], "helper")[0] == 403       # scenario roles may_spawn nothing
    call(env, "POST", f"/instances/{parent['session_id']}/exit")
    status, body = spawn(env, parent["session_id"], "helper")
    assert status == 403 and "not active" in body["error"]


# --- instances ---------------------------------------------------------------------------------

def test_list_instances_filters_and_includes_scenario(env):
    make_role(env, WORKER); make_role(env, HELPER)
    call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    w = join(env, "worker")[1]
    h = spawn(env, w["session_id"], "helper")[1]
    call(env, "POST", f"/instances/{h['session_id']}/exit")
    everything = call(env, "GET", "/instances")[1]["instances"]
    assert {i["role"] for i in everything} == {"a", "worker", "helper"}
    assert [i["role"] for i in call(env, "GET", "/instances", query={"role": "a"})[1]["instances"]] == ["a"]
    assert [i["session_id"] for i in call(env, "GET", "/instances", query={"status": "exited"})[1]["instances"]] \
        == [h["session_id"]]
    assert call(env, "GET", "/instances", query={"status": "nope"})[0] == 400
    shape = next(i for i in everything if i["session_id"] == w["session_id"])
    assert shape["agent_id"] == w["agent_id"] and shape["tools"] == WORKER["tools"] and shape["exposures"] == 0


def test_heartbeat_updates_last_seen_and_refuses_ended_instances(env):
    make_role(env, WORKER)
    sid = join(env, "worker")[1]["session_id"]
    env.ddb.Table("burn-sessions").update_item(Key={"session_id": sid}, UpdateExpression="SET last_seen = :t",
                                               ExpressionAttributeValues={":t": "2020-01-01T00:00:00.000Z"})
    status, body = call(env, "POST", f"/instances/{sid}/heartbeat")
    assert status == 200 and body["last_seen"] > "2025" and body["status"] == "active"
    assert call(env, "POST", "/instances/sess_nope/heartbeat")[0] == 404
    call(env, "POST", f"/instances/{sid}/exit")
    status, body = call(env, "POST", f"/instances/{sid}/heartbeat")
    assert status == 409 and "exited" in body["error"]
    assert session_row(env, sid)["status"] == "exited"


def test_exit_revokes_badge_deletes_credentials_and_emits(env):
    make_role(env, WORKER)
    body = join(env, "worker")[1]
    status, out = call(env, "POST", f"/instances/{body['session_id']}/exit")
    assert status == 200 and out["status"] == "exited" and out["exited_at"]
    assert call(env, "GET", f"/badge/{body['session_id']}")[1]["status"] == "exited"
    assert len(env.badges.deleted) == 1
    assert ("DELETE", f"/agents/{body['agent_id']}/credentials", None) in env.minter.calls
    exited = [e for e in call(env, "GET", "/events")[1]["events"] if e["action"] == "exited"]
    assert len(exited) == 1 and exited[0]["source"] == "instance" and exited[0]["role"] == "worker"
    # exiting again is safe: no second event, no second badge deletion
    assert call(env, "POST", f"/instances/{body['session_id']}/exit")[0] == 200
    assert len([e for e in call(env, "GET", "/events")[1]["events"] if e["action"] == "exited"]) == 1
    assert len(env.badges.deleted) == 1
    assert call(env, "POST", "/instances/sess_nope/exit")[0] == 404


def test_scenario_exit_keeps_the_agents_credentials(env):
    sid = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]["session_id"]
    assert call(env, "POST", f"/instances/{sid}/exit")[1]["status"] == "exited"
    assert not [c for c in env.minter.calls if c[0] == "DELETE"]


def test_exit_does_not_resurrect_a_burned_instance(env):
    make_role(env, WORKER)
    body = join(env, "worker")[1]
    call(env, "POST", "/burn", {"agent_id": body["agent_id"]})
    out = call(env, "POST", f"/instances/{body['session_id']}/exit")[1]
    assert out["status"] == "revoked"
    assert not [c for c in env.minter.calls if c[0] == "DELETE"]


def test_idle_sweep_exits_only_stale_active_role_instances(env, monkeypatch):
    make_role(env, WORKER)
    stale, fresh = join(env, "worker", uses=2)[1], join(env, "worker")[1]
    sid_scenario = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]["session_id"]
    old = (datetime.now(timezone.utc) - timedelta(seconds=601)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for sid in (stale["session_id"], sid_scenario):
        env.ddb.Table("burn-sessions").update_item(Key={"session_id": sid}, UpdateExpression="SET last_seen = :t",
                                                   ExpressionAttributeValues={":t": old})
    listed = {i["session_id"]: i["status"] for i in call(env, "GET", "/instances")[1]["instances"]}
    assert listed[stale["session_id"]] == "exited"
    assert listed[fresh["session_id"]] == "active" and listed[sid_scenario] == "active"
    assert ("DELETE", f"/agents/{stale['agent_id']}/credentials", None) in env.minter.calls


def test_idle_window_and_cap_are_configurable(env, monkeypatch):
    import instances
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 3})[1]["token"]
    for _ in range(3):
        call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})
    monkeypatch.setenv("BURN_INSTANCE_IDLE_SECONDS", "1")
    import time; time.sleep(1.1)
    assert len(instances.sweep_idle(limit=2)) == 2           # capped
    assert len(instances.sweep_idle(limit=2)) == 1           # the rest on the next call
    monkeypatch.setenv("BURN_INSTANCE_IDLE_SECONDS", "junk")
    assert instances.idle_seconds() == 600


def test_sweep_survives_a_failing_exit(env):
    make_role(env, WORKER)
    sid = join(env, "worker")[1]["session_id"]
    env.ddb.Table("burn-sessions").update_item(Key={"session_id": sid}, UpdateExpression="SET last_seen = :t",
                                               ExpressionAttributeValues={":t": "2020-01-01T00:00:00.000Z"})
    env.minter.fail = True
    assert call(env, "GET", "/instances")[0] == 200   # the listing does not fail
    assert session_row(env, sid)["status"] == "exited"  # the badge was still ended


# --- burn on instance ids, throttling, CORS ----------------------------------------------------

def test_post_burn_and_reads_work_for_an_instance_agent_id(env):
    make_role(env, WORKER)
    body = join(env, "worker")[1]
    agent_id = body["agent_id"]
    env.ddb.Table("burn-exposures").put_item(Item={
        "agent_id": agent_id, "secret_ref_or_fp": f"burn/{agent_id}/api_key", "secret_ref": f"burn/{agent_id}/api_key",
        "fingerprint": "a" * 64, "secret_type": "api_key", "owner": "agent", "source": "tool_response",
        "first_seen": "2026-01-01T00:00:00Z"})
    status, out = call(env, "POST", "/burn", {"agent_id": agent_id})
    assert status == 200 and out["sessions_revoked"] == [body["session_id"]]
    assert out["rotated"] == [f"burn/{agent_id}/api_key"]
    assert call(env, "GET", f"/badge/{body['session_id']}")[1]["status"] == "revoked"
    assert call(env, "GET", f"/agents/{agent_id}/ledger")[0] == 200
    assert call(env, "GET", f"/burn/{agent_id}/blast-radius")[0] == 200
    assert call(env, "POST", "/burn", {"agent_id": "agent-worker-000000"})[0] == 404


def test_throttling_is_429_with_a_retry_hint_not_500(env):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {})[1]["token"]
    env.minter.throttle = True
    import handler
    out = handler.handler({"requestContext": {"http": {"method": "POST", "path": "/sessions"}},
                           "headers": {"x-burn-demo-token": env.token}, "queryStringParameters": None,
                           "body": '{"role": "worker", "enroll_token": "%s"}' % token})
    assert out["statusCode"] == 429 and out["headers"]["retry-after"]
    assert "retry_after_seconds" in out["body"]
    env.minter.throttle = False
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 201  # use was refunded


def test_aws_throttle_errors_map_to_429(env, monkeypatch):
    from botocore.exceptions import ClientError
    import badges
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {})[1]["token"]
    err = ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "x"}}, "CreateUserPoolClient")
    monkeypatch.setattr(badges, "create_badge", lambda *a: (_ for _ in ()).throw(err))
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 429


def test_new_routes_need_the_token_and_preflight_allows_put_and_delete(env):
    for method, path in [("GET", "/roles"), ("PUT", "/roles/x"), ("DELETE", "/tokens/x"),
                         ("GET", "/instances"), ("POST", "/burns"), ("POST", "/instances/s/exit")]:
        assert call(env, method, path, token="wrong")[0] == 401, (method, path)
    import handler
    out = handler.handler({"requestContext": {"http": {"method": "OPTIONS", "path": "/roles/x"}},
                           "headers": {"origin": "http://localhost:5173"}})
    assert out["statusCode"] == 204
    assert {"PUT", "DELETE"} <= set(out["headers"]["access-control-allow-methods"].split(","))


def test_failed_join_cleans_up_what_it_created_and_refunds_the_token(env, monkeypatch):
    from botocore.exceptions import ClientError
    import badges
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 1})[1]["token"]
    err = ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "x"}}, "CreateUserPoolClient")
    real_create = badges.create_badge
    monkeypatch.setattr(badges, "create_badge", lambda *a: (_ for _ in ()).throw(err))
    status, _ = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})
    assert status == 429
    deletes = [c for c in env.minter.calls if c[0] == "DELETE"]
    assert len(deletes) == 1 and deletes[0][1].startswith("/agents/agent-worker-")
    minted = [c for c in env.minter.calls if c[0] == "POST"][0][1]
    assert deletes[0][1] == minted  # same agent id that was issued credentials
    assert env.ddb.Table("burn-sessions").scan()["Items"] == []
    monkeypatch.setattr(badges, "create_badge", real_create)
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 201  # use refunded


def test_failure_after_the_badge_exists_deletes_the_badge_too(env, monkeypatch):
    import sessions
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 1})[1]["token"]

    def boom(agent_id, badge):
        raise RuntimeError("secrets manager down")

    real_store = sessions._store_badge
    monkeypatch.setattr(sessions, "_store_badge", boom)
    with pytest.raises(RuntimeError):
        call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})
    assert len(env.badges.created) == 1 and len(env.badges.deleted) == 1
    assert [c[0] for c in env.minter.calls] == ["POST", "POST", "DELETE"]  # credentials, canary, cleanup
    monkeypatch.setattr(sessions, "_store_badge", real_store)
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 201


# --- the idle sweep must not slow GET /instances down -------------------------------------------

import minter_client  # noqa: E402

REAL_CALL_ASYNC = minter_client.call_async  # the fixture swaps the module attribute for a fake


def make_stale(env, count):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": count})[1]["token"]
    sids = [call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[1]["session_id"]
            for _ in range(count)]
    for sid in sids:
        env.ddb.Table("burn-sessions").update_item(
            Key={"session_id": sid}, UpdateExpression="SET last_seen = :t",
            ExpressionAttributeValues={":t": "2020-01-01T00:00:00.000Z"})
    return sids


def test_sweep_queues_slow_credential_deletes_instead_of_waiting(env):
    import time
    sids = make_stale(env, 8)
    env.minter.delay = 5.0  # every awaited minter call now takes 5 s; awaiting 8 would take 40 s
    started = time.monotonic()
    status, body = call(env, "GET", "/instances")
    assert status == 200 and time.monotonic() - started < 10
    assert {i["status"] for i in body["instances"]} == {"exited"} and len(body["instances"]) == 8
    assert sorted(q[1] for q in env.minter.queued) == sorted(
        f"/agents/{i['agent_id']}/credentials" for i in body["instances"])
    assert all(q[0] == "DELETE" for q in env.minter.queued) and len(sids) == 8


def test_sweep_stops_at_its_time_budget_and_finishes_on_later_calls(env):
    import time, instances
    make_stale(env, 6)
    env.badges.deleted.clear()
    real = env.badges.delete_badge
    env.badges.delete_badge = lambda client_id: (time.sleep(0.3), real(client_id))
    try:
        started = time.monotonic()
        first = instances.sweep_idle(limit=10, budget=0.5)
        assert 0 < len(first) < 6 and time.monotonic() - started < 1.5
        second = instances.sweep_idle(limit=10, budget=30)
        assert len(first) + len(second) == 6
    finally:
        env.badges.delete_badge = real


def test_a_credential_delete_that_could_not_be_queued_is_retried_by_exit(env, monkeypatch):
    sids = make_stale(env, 1)

    def cannot_queue(method, path, body=None):
        raise minter_client.MinterError("not queued")

    monkeypatch.setattr(minter_client, "call_async", cannot_queue)
    assert call(env, "GET", "/instances")[0] == 200
    assert session_row(env, sids[0])["status"] == "exited"
    agent_id = session_row(env, sids[0])["agent_id"]
    assert ("DELETE", f"/agents/{agent_id}/credentials", None) not in env.minter.calls
    assert call(env, "POST", f"/instances/{sids[0]}/exit")[0] == 200  # awaits the delete
    assert ("DELETE", f"/agents/{agent_id}/credentials", None) in env.minter.calls


def test_call_async_uses_an_event_invoke():
    sent = {}

    class Stub:
        def invoke(self, **kwargs):
            sent.update(kwargs)
            return {"StatusCode": 202}

    minter_client._lambda = Stub()
    try:
        REAL_CALL_ASYNC("DELETE", "/agents/agent-x/credentials")
    finally:
        minter_client._lambda = None
    assert sent["InvocationType"] == "Event" and sent["FunctionName"] == "burn-minter"
    assert b'"DELETE"' in sent["Payload"]


def test_listing_is_capped_to_the_newest_rows_and_counts_exposures(env, monkeypatch):
    import instances
    monkeypatch.setattr(instances, "MAX_LISTED", 3)
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 5})[1]["token"]
    bodies = [call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[1] for _ in range(5)]
    for n, body in enumerate(bodies):  # distinct created_at so "newest" is well defined
        env.ddb.Table("burn-sessions").update_item(
            Key={"session_id": body["session_id"]}, UpdateExpression="SET created_at = :t",
            ExpressionAttributeValues={":t": f"2026-01-01T00:00:0{n}.000Z"})
    env.ddb.Table("burn-exposures").put_item(Item={
        "agent_id": bodies[4]["agent_id"], "secret_ref_or_fp": "fp#x", "fingerprint": "a" * 64})
    listed = call(env, "GET", "/instances")[1]["instances"]
    assert [i["session_id"] for i in listed] == [b["session_id"] for b in reversed(bodies[2:])]
    assert [i["exposures"] for i in listed] == [1, 0, 0]
    assert set(listed[0]) >= {"session_id", "agent_id", "role", "status", "tools", "created_at", "exposures"}


def test_exposure_counts_run_concurrently(env, monkeypatch):
    import time, instances
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 12})[1]["token"]
    for _ in range(12):
        call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})
    call(env, "GET", "/instances")  # warm: threads build their DynamoDB resource once
    real = instances._exposures
    monkeypatch.setattr(instances, "_exposures", lambda agent_id: (time.sleep(0.2), real(agent_id))[1])
    started = time.monotonic()
    assert len(call(env, "GET", "/instances")[1]["instances"]) == 12
    assert time.monotonic() - started < 1.2  # sequential would be 2.4 s


# --- a spawn or join must not escape a burn that is running ------------------------------------

def test_child_is_refused_and_revoked_if_its_parent_is_burned_while_it_starts(env, monkeypatch):
    import sessions
    make_role(env, WORKER); make_role(env, HELPER)
    parent = join(env, "worker")[1]
    real_create_badge = sessions.badges.create_badge

    def burn_parent_midway(agent_id, session_id):
        # the parent is revoked after the spawn was allowed, before the child row is written
        sessions.end_session(session_row(env, parent["session_id"]), "revoked")
        return real_create_badge(agent_id, session_id)

    monkeypatch.setattr(sessions.badges, "create_badge", burn_parent_midway)
    status, body = spawn(env, parent["session_id"], "helper")
    assert status == 403 and "not active" in body["error"]
    children = [r for r in env.ddb.Table("burn-sessions").scan()["Items"] if r.get("parent_session")]
    assert len(children) == 1 and children[0]["status"] == "revoked"
    assert any(c[0] == "DELETE" and c[1] == f"/agents/{children[0]['agent_id']}/credentials"
               for c in env.minter.calls)
    assert children[0]["client_id"] in env.badges.deleted


def test_join_is_refused_if_the_token_is_revoked_while_it_starts(env, monkeypatch):
    import sessions
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 2})[1]
    real_create_badge = sessions.badges.create_badge

    def revoke_midway(agent_id, session_id):
        call(env, "DELETE", f"/tokens/{token['token_id']}")
        return real_create_badge(agent_id, session_id)

    monkeypatch.setattr(sessions.badges, "create_badge", revoke_midway)
    status, body = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})
    assert status == 403 and "revoked" in body["error"]
    rows = env.ddb.Table("burn-sessions").scan()["Items"]
    assert len(rows) == 1 and rows[0]["status"] == "revoked"


def test_failing_event_writes_do_not_undo_a_join_or_refund_its_use(env, monkeypatch):
    import db
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 1})[1]
    real = db.write_event

    def flaky(**fields):
        if fields["action"] == "started":
            raise RuntimeError("events table throttled")
        return real(**fields)

    monkeypatch.setattr(db, "write_event", flaky)
    status, body = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})
    assert status == 201 and session_row(env, body["session_id"])["status"] == "active"
    row = env.ddb.Table("burn-enroll-tokens").get_item(Key={"token_id": token["token_id"]})["Item"]
    assert row["uses_left"] == 0
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[0] == 403


def test_new_instances_use_the_same_timestamp_format_as_heartbeat(env):
    import re
    make_role(env, WORKER)
    row = session_row(env, join(env, "worker")[1]["session_id"])
    pattern = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$"
    assert re.match(pattern, row["created_at"]) and re.match(pattern, row["last_seen"])


def test_a_heartbeat_between_the_sweep_scan_and_its_update_wins(env, monkeypatch):
    import db, instances
    sids = make_stale(env, 2)
    real = db.scan_all

    def scan_then_heartbeat(tbl, **kwargs):
        rows = real(tbl, **kwargs)
        if "FilterExpression" in kwargs and rows:  # the sweep's scan; one instance checks in right after it
            call(env, "POST", f"/instances/{sids[0]}/heartbeat")
        return rows

    monkeypatch.setattr(db, "scan_all", scan_then_heartbeat)
    exited = instances.sweep_idle()
    assert exited == [sids[1]]
    assert session_row(env, sids[0])["status"] == "active" and session_row(env, sids[1])["status"] == "exited"
    assert ("DELETE", f"/agents/{session_row(env, sids[0])['agent_id']}/credentials", None) not in env.minter.calls


# --- canaries and auto_burn on roles -----------------------------------------------------------

def canary_calls(env):
    return [c for c in env.minter.calls if c[1].endswith("/canary")]


def test_role_canary_flags_are_stored_returned_and_validated(env):
    assert make_role(env, WORKER)["canaries"] is True and make_role(env, HELPER)["auto_burn"] is True  # defaults
    quiet = make_role(env, {"name": "quiet", "tools": [], "auto_burn": False, "canaries": False})
    assert quiet["auto_burn"] is False and quiet["canaries"] is False
    row = env.ddb.Table("burn-roles").get_item(Key={"name": "quiet"})["Item"]
    assert row["auto_burn"] is False and row["canaries"] is False
    got = call(env, "GET", "/roles/quiet")[1]
    assert got["auto_burn"] is False and got["canaries"] is False
    status, saved = call(env, "PUT", "/roles/quiet", {"name": "quiet", "tools": [], "auto_burn": True})
    assert status == 200 and saved["auto_burn"] is True and saved["canaries"] is True
    assert call(env, "POST", "/roles", {"name": "x", "tools": [], "auto_burn": "no"})[0] == 400
    assert call(env, "POST", "/roles", {"name": "x", "tools": [], "canaries": 1})[0] == 400
    roles = {r["name"]: r for r in call(env, "GET", "/roles")[1]["roles"]}
    assert roles["a"]["canaries"] is True and roles["a"]["auto_burn"] is True  # scenario roles


def test_a_role_stored_without_the_flags_reads_as_on(env):
    env.ddb.Table("burn-roles").put_item(Item={"name": "old", "tools": [], "managed_by": "ui"})
    got = call(env, "GET", "/roles/old")[1]
    assert got["canaries"] is True and got["auto_burn"] is True


def test_each_join_and_scenario_start_issues_exactly_one_canary(env):
    make_role(env, WORKER)
    body = join(env, "worker")[1]
    issued = canary_calls(env)
    assert len(issued) == 1 and issued[0][1] == f"/sessions/{body['session_id']}/canary"
    assert issued[0][2] == {"agent_id": body["agent_id"]}
    scenario = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    issued = canary_calls(env)
    assert len(issued) == 2 and issued[1][1] == f"/sessions/{scenario['session_id']}/canary"


def test_a_spawned_child_gets_its_own_canary(env):
    make_role(env, WORKER); make_role(env, HELPER)
    parent = join(env, "worker")[1]
    child = spawn(env, parent["session_id"], "helper")[1]
    assert [c[1] for c in canary_calls(env)] == [f"/sessions/{parent['session_id']}/canary",
                                                 f"/sessions/{child['session_id']}/canary"]


def test_a_role_with_canaries_off_issues_none(env):
    make_role(env, {"name": "plain", "tools": ["notify"], "canaries": False})
    assert join(env, "plain")[0] == 201
    assert canary_calls(env) == []


def test_a_failed_canary_leaves_no_session_and_cleans_up(env):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 1})[1]["token"]
    real = env.minter.__call__

    def canary_fails(method, path, body=None, query=None):
        if path.endswith("/canary"):
            raise minter_client.MinterError("canary failed")
        return real(method, path, body, query)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(minter_client, "call", canary_fails)
    try:
        assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 502
    finally:
        monkey.undo()
    assert env.ddb.Table("burn-sessions").scan()["Items"] == [] and env.badges.created == []
    assert [c for c in env.minter.calls if c[0] == "DELETE"]  # the minted credentials were removed
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 201  # use refunded


# --- the join reply carries the instance's own secret values ----------------------------------

def stored_values(env, body):
    """The api_key / aws_key values the (fake) minter put in Secrets Manager for this instance."""
    import json
    agent = body["agent_id"]
    key = env.minter.stored.get(f"burn/{agent}/aws_key")
    return {"api_key": env.minter.stored.get(f"burn/{agent}/api_key"),
            "aws_key": json.loads(key) if key else None}


def test_join_returns_the_values_the_minter_stored_and_the_badge(env):
    make_role(env, WORKER)
    status, body, _ = join(env, "worker")
    assert status == 201
    stored = stored_values(env, body)
    assert body["secrets"]["api_key"] == stored["api_key"]
    assert body["secrets"]["aws_key"] == {"access_key_id": stored["aws_key"]["access_key_id"],
                                          "secret_access_key": stored["aws_key"]["secret_access_key"]}
    import boto3, json
    badge = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId=body["badge_ref"])["SecretString"])
    assert body["secrets"]["badge"] == badge and set(badge) == {"client_id", "client_secret", "token_url", "scope"}
    assert "iam_user" not in str(body["secrets"])


def test_spawn_returns_only_what_the_narrowed_child_has(env):
    make_role(env, WORKER); make_role(env, HELPER)
    parent = join(env, "worker")[1]
    child = spawn(env, parent["session_id"], "helper")[1]
    assert set(child["secrets"]) == {"badge", "api_key"}  # helper mints api_key only
    assert child["secrets"]["api_key"] == stored_values(env, child)["api_key"]
    assert child["secrets"]["api_key"] != parent["secrets"]["api_key"]  # its own key, not the parent's


def test_only_what_the_role_mints_is_returned(env):
    make_role(env, {"name": "bare", "tools": ["notify"], "credentials": []})
    assert set(join(env, "bare")[1]["secrets"]) == {"badge"}


def test_the_scenario_path_has_no_secrets(env):
    body = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    assert "secrets" not in body


def test_values_are_not_stored_in_the_session_row_or_events(env):
    make_role(env, WORKER)
    body = join(env, "worker")[1]
    values = [body["secrets"]["api_key"], body["secrets"]["aws_key"]["secret_access_key"],
              body["secrets"]["badge"]["client_secret"]]
    persisted = str(env.ddb.Table("burn-sessions").scan()["Items"]) + str(env.ddb.Table("burn-events").scan()["Items"])
    assert not any(v in persisted for v in values)
    assert call(env, "GET", "/instances")[0] == 200
    assert not any(v in str(call(env, "GET", "/instances")[1]) for v in values)


def test_a_secret_that_cannot_be_read_discards_the_instance_and_refunds_the_token(env, monkeypatch):
    from botocore.exceptions import ClientError
    import sessions
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 1})[1]["token"]
    real = sessions._read_secret

    def denied(client, ref):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "GetSecretValue")

    monkeypatch.setattr(sessions, "_read_secret", denied)
    with pytest.raises(ClientError):
        call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})
    assert env.ddb.Table("burn-sessions").scan()["Items"] == []
    assert len(env.badges.created) == 1 and len(env.badges.deleted) == 1
    assert [c for c in env.minter.calls if c[0] == "DELETE" and c[1].endswith("/credentials")]
    monkeypatch.setattr(sessions, "_read_secret", real)
    assert call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[0] == 201  # use refunded


def test_reading_a_fresh_secret_retries_briefly_then_gives_up(monkeypatch):
    from botocore.exceptions import ClientError
    import sessions
    monkeypatch.setattr(sessions.time, "sleep", lambda s: None)

    class Client:
        def __init__(self, failures, code):
            self.failures, self.code, self.calls = failures, code, 0

        def get_secret_value(self, SecretId):
            self.calls += 1
            if self.calls <= self.failures:
                raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "GetSecretValue")
            return {"SecretString": "value"}

    assert sessions._read_secret(Client(2, "ResourceNotFoundException"), "burn/agent-x/api_key") == "value"
    assert sessions._read_secret(Client(1, "ThrottlingException"), "burn/agent-x/api_key") == "value"
    stuck = Client(99, "ResourceNotFoundException")
    with pytest.raises(ClientError):
        sessions._read_secret(stuck, "burn/agent-x/api_key")
    assert stuck.calls == sessions.SECRET_READ_ATTEMPTS
    denied = Client(99, "AccessDeniedException")
    with pytest.raises(ClientError):
        sessions._read_secret(denied, "burn/agent-x/api_key")
    assert denied.calls == 1  # not retried


def test_secret_values_never_reach_stdout_or_logs(env, monkeypatch, capsys, caplog):
    import logging
    import sessions
    # INFO and up, like the Lambda's default. (botocore at DEBUG prints raw response bodies; that is
    # the SDK's own logging and must stay off in production.)
    caplog.set_level(logging.INFO)
    make_role(env, WORKER)
    body = join(env, "worker")[1]
    values = [body["secrets"]["api_key"], body["secrets"]["aws_key"]["secret_access_key"],
              body["secrets"]["aws_key"]["access_key_id"], body["secrets"]["badge"]["client_secret"]]
    # a failing join (event writes failing too) must not print them either
    import db
    real_event = db.write_event
    monkeypatch.setattr(db, "write_event", lambda **f: (_ for _ in ()).throw(RuntimeError("down")))
    other = join(env, "worker")[1]
    monkeypatch.setattr(db, "write_event", real_event)
    values += [other["secrets"]["api_key"], other["secrets"]["aws_key"]["secret_access_key"]]
    out = capsys.readouterr()
    for value in values:
        assert value not in out.out and value not in out.err and value not in caplog.text
