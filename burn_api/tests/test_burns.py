"""POST /burns: instance, role, lineage and token scopes."""
import time

from conftest import call
from test_swarm import HELPER, WORKER, join, make_role, spawn

def status_of(env, session_id):
    return call(env, "GET", f"/badge/{session_id}")[1]["status"]


def tree(env):
    """worker (root) -> helper (child) -> helper (grandchild); plus one unrelated worker."""
    make_role(env, WORKER | {"may_spawn": ["helper"]})
    make_role(env, HELPER | {"may_spawn": ["helper"]})
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 2})[1]
    root = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[1]
    other = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[1]
    child = spawn(env, root["session_id"], "helper")[1]
    grandchild = spawn(env, child["session_id"], "helper")[1]
    return token, root, other, child, grandchild


def test_instance_scope(env):
    _, root, other, child, _ = tree(env)
    status, out = call(env, "POST", "/burns", {"scope": "instance", "id": child["session_id"], "reason": "r"})
    assert status == 200 and out["scope"] == "instance" and out["failed"] == []
    assert [b["agent_id"] for b in out["burned"]] == [child["agent_id"]]
    assert out["burned"][0]["sessions_revoked"] == [child["session_id"]]
    assert status_of(env, child["session_id"]) == "revoked"
    assert status_of(env, root["session_id"]) == "active" and status_of(env, other["session_id"]) == "active"
    assert ("POST", f"/agents/{child['agent_id']}/rotate") in [(c[0], c[1]) for c in env.minter.calls]


def test_role_scope_burns_every_instance_of_the_role_only(env):
    _, root, other, child, grandchild = tree(env)
    out = call(env, "POST", "/burns", {"scope": "role", "id": "helper"})[1]
    assert {b["agent_id"] for b in out["burned"]} == {child["agent_id"], grandchild["agent_id"]}
    assert status_of(env, child["session_id"]) == status_of(env, grandchild["session_id"]) == "revoked"
    assert status_of(env, root["session_id"]) == "active"


def test_role_scope_covers_a_scenario_role(env):
    sid = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]["session_id"]
    other = call(env, "POST", "/sessions", {"agent_id": "agent-b"})[1]["session_id"]
    out = call(env, "POST", "/burns", {"scope": "role", "id": "a"})[1]
    assert [b["agent_id"] for b in out["burned"]] == ["agent-a"]
    assert status_of(env, sid) == "revoked" and status_of(env, other) == "active"


def test_lineage_scope_is_the_instance_and_its_descendants(env):
    _, root, other, child, grandchild = tree(env)
    out = call(env, "POST", "/burns", {"scope": "lineage", "id": child["session_id"]})[1]
    assert {b["agent_id"] for b in out["burned"]} == {child["agent_id"], grandchild["agent_id"]}
    assert status_of(env, root["session_id"]) == "active"   # ancestors are not part of the lineage
    out = call(env, "POST", "/burns", {"scope": "lineage", "id": root["session_id"]})[1]
    assert [b["agent_id"] for b in out["burned"]] == [root["agent_id"]]  # descendants were already revoked
    assert status_of(env, other["session_id"]) == "active"


def test_token_scope_burns_joined_instances_and_revokes_the_token(env):
    token, root, other, child, _ = tree(env)
    out = call(env, "POST", "/burns", {"scope": "token", "id": token["token_id"]})[1]
    assert {b["agent_id"] for b in out["burned"]} == {root["agent_id"], other["agent_id"]}
    assert status_of(env, root["session_id"]) == status_of(env, other["session_id"]) == "revoked"
    assert status_of(env, child["session_id"]) == "active"   # a child did not join with the token
    assert call(env, "POST", "/roles/worker/tokens", {})[0] == 201
    new = call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})
    assert new[0] == 403 and "revoked" in new[1]["error"]


def test_targets_are_revoked_before_any_rotation(env):
    _, root, other, *_ = tree(env)
    seen = []
    real = env.minter.__call__

    def spy(method, path, body=None, query=None):
        if path.endswith("/rotate"):
            seen.append({s["session_id"]: s["status"] for s in env.ddb.Table("burn-sessions").scan()["Items"]})
        return real(method, path, body, query)

    import minter_client
    minter_client.call = spy  # conftest's monkeypatch restores it
    call(env, "POST", "/burns", {"scope": "role", "id": "worker"})
    assert seen and all(s[root["session_id"]] == s[other["session_id"]] == "revoked" for s in seen)


def test_one_failing_burn_is_reported_and_does_not_stop_the_others(env):
    _, root, other, *_ = tree(env)
    env.minter.rotate_fail.add(root["agent_id"])
    status, out = call(env, "POST", "/burns", {"scope": "role", "id": "worker"})
    assert status == 200
    assert [b["agent_id"] for b in out["burned"]] == [other["agent_id"]]
    assert [f["agent_id"] for f in out["failed"]] == [root["agent_id"]]
    assert "revoked" in out["failed"][0]["error"] and "rotation failed" not in out["failed"][0]["error"]
    assert status_of(env, root["session_id"]) == "revoked"  # revoked first, so it is stopped regardless


def test_slow_burns_are_cut_off_at_the_budget(env, monkeypatch):
    import burn, burn_scopes
    make_role(env, WORKER)
    for _ in range(3):
        join(env, "worker")
    monkeypatch.setattr(burn, "burn_agent", lambda agent_id, reason="": time.sleep(4))
    started = time.monotonic()
    out = burn_scopes.burn_scope("role", "worker", budget=1.5)
    assert time.monotonic() - started < 3
    assert out["burned"] == [] and len(out["failed"]) == 3 and "timed out" in out["failed"][0]["error"]
    assert all(i["status"] == "revoked" for i in call(env, "GET", "/instances")[1]["instances"])


def test_twenty_instances_burn_in_one_call(env):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 20})[1]["token"]
    ids = [call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[1]["agent_id"] for _ in range(20)]
    out = call(env, "POST", "/burns", {"scope": "role", "id": "worker"})[1]
    assert sorted(b["agent_id"] for b in out["burned"]) == sorted(ids) and out["failed"] == []
    assert all(b["revoked"] for b in out["burned"])
    revoked = [e for e in call(env, "GET", "/events", query={"limit": 500})[1]["events"] if e["action"] == "revoked"]
    assert len(revoked) == 20


def test_burn_validation_and_404s(env):
    tree(env)
    assert call(env, "POST", "/burns", {"scope": "everything", "id": "x"})[0] == 400
    assert call(env, "POST", "/burns", {"scope": "role"})[0] == 400
    assert call(env, "POST", "/burns", {"scope": "instance", "id": "sess_nope"})[0] == 404
    assert call(env, "POST", "/burns", {"scope": "lineage", "id": "sess_nope"})[0] == 404
    assert call(env, "POST", "/burns", {"scope": "role", "id": "ghost"})[0] == 404
    assert call(env, "POST", "/burns", {"scope": "token", "id": "ffffffffffffffff"})[0] == 404


def test_burning_a_role_with_no_active_instances_is_empty_not_an_error(env):
    make_role(env, WORKER)
    out = call(env, "POST", "/burns", {"scope": "role", "id": "worker"})
    assert out[0] == 200 and out[1]["burned"] == [] and out[1]["failed"] == []


def test_throttled_burn_agents_are_reported_as_failed(env):
    make_role(env, WORKER)
    join(env, "worker")
    env.minter.throttle = True
    out = call(env, "POST", "/burns", {"scope": "role", "id": "worker"})[1]
    assert out["burned"] == [] and "rate limited" in out["failed"][0]["error"]


def test_child_written_after_the_first_scan_is_still_revoked_and_burned(env, monkeypatch):
    import db
    _, root, other, child, grandchild = tree(env)
    real = db.sessions_for_parents
    calls = []

    def scan_then_race(parents):
        found = real(parents)
        if not calls:  # a spawn lands right after the burn's first look at the tree
            late = dict(next(r for r in found if r["session_id"] == child["session_id"]))
            late.update(session_id="sess_late", agent_id="agent-helper-abcdef", parent_session=root["session_id"])
            env.ddb.Table("burn-sessions").put_item(Item=late)
        calls.append(parents)
        return found

    monkeypatch.setattr(db, "sessions_for_parents", scan_then_race)
    out = call(env, "POST", "/burns", {"scope": "lineage", "id": root["session_id"]})[1]
    assert status_of(env, "sess_late") == "revoked"
    assert "agent-helper-abcdef" in {b["agent_id"] for b in out["burned"]}
    assert status_of(env, other["session_id"]) == "active"


def test_a_self_spawning_chain_55_deep_is_burned_completely(env):
    make_role(env, WORKER | {"may_spawn": ["helper"]})
    make_role(env, HELPER | {"may_spawn": ["helper"]})
    root = join(env, "worker")[1]
    chain, parent = [root], root
    for _ in range(55):
        parent = spawn(env, parent["session_id"], "helper")[1]
        chain.append(parent)
    out = call(env, "POST", "/burns", {"scope": "lineage", "id": root["session_id"]})[1]
    assert out["failed"] == [] and len(out["burned"]) == 56
    assert all(status_of(env, s["session_id"]) == "revoked" for s in chain)


def test_a_throttled_revoke_does_not_lose_agents(env, monkeypatch):
    import threading
    import sessions
    from botocore.exceptions import ClientError
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 3})[1]["token"]
    made = [call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[1] for _ in range(3)]
    real, lock, count = sessions.end_session, threading.Lock(), [0]

    def throttle_second(session, status="revoked", **kw):
        with lock:
            count[0] += 1
            n = count[0]
        if n == 2:
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "UpdateItem")
        return real(session, status, **kw)

    monkeypatch.setattr(sessions, "end_session", throttle_second)
    status, out = call(env, "POST", "/burns", {"scope": "role", "id": "worker"})
    assert status == 200
    accounted = {b["agent_id"] for b in out["burned"]} | {f["agent_id"] for f in out["failed"]}
    assert accounted == {m["agent_id"] for m in made}
    assert out["failed"] == []  # the burn itself revokes the session whose fast revoke was throttled
    assert all(status_of(env, m["session_id"]) == "revoked" for m in made)
    assert sum(len(b["sessions_revoked"]) for b in out["burned"]) == 3


def test_revokes_that_miss_the_deadline_are_reported(env, monkeypatch):
    import time, burn_scopes, sessions
    make_role(env, WORKER)
    for _ in range(2):
        join(env, "worker")
    real = sessions.end_session
    monkeypatch.setattr(sessions, "end_session", lambda *a, **k: (time.sleep(2), real(*a, **k))[1])
    started = time.monotonic()
    out = burn_scopes.burn_scope("role", "worker", budget=0.5)
    assert time.monotonic() - started < 1.5
    assert out["burned"] == [] and len(out["failed"]) == 2
    assert all("revoke not confirmed" in f["error"] for f in out["failed"])
