"""Replace on burn in the API: the role setting, progress on the heartbeat, reads, and linking a replacement."""
import threading

import pytest
from burn_contracts import names
from conftest import call
from test_auth import as_instance
from test_swarm import join, make_role, session_row

ROLE = {"name": "crawler", "tools": ["read_inbox"], "credentials": ["api_key"], "replace_on_burn": True}


def fresh(env, role="crawler"):
    status, body, _ = join(env, role)
    assert status == 201, body
    return body


def beat(env, sid, *events, raw=None):
    body = raw if raw is not None else {"progress": [{"item": i, "state": s} for i, s in events]}
    return call(env, "POST", f"/instances/{sid}/heartbeat", body)


def mark_burned(env, sid, *, status="pending", done=(), quarantined=(), revoked_at="2026-11-07T10:00:00.000Z",
                generation=None):
    """A burned instance as the burn leaves it (the burn-time hook is tested with the burn itself)."""
    names_, values = {"#s": "status"}, {":r": "revoked", ":t": revoked_at, ":rep": {
        "status": status, "cutoff": revoked_at, "cutoff_source": "burn_time",
        "done": list(done), "quarantined": list(quarantined)}}
    expr = "SET #s = :r, revoked_at = :t, replacement = :rep"
    if generation is not None:
        expr += ", generation = :g"
        values[":g"] = generation
    env.ddb.Table("burn-sessions").update_item(Key={"session_id": sid}, UpdateExpression=expr,
                                               ExpressionAttributeNames=names_, ExpressionAttributeValues=values)


def backdate(env, sid, created_at="2026-11-07T09:00:00.000Z"):
    env.ddb.Table("burn-sessions").update_item(Key={"session_id": sid}, UpdateExpression="SET created_at = :c",
                                               ExpressionAttributeValues={":c": created_at})


def events(env, action):
    rows = env.ddb.Table("burn-events").scan()["Items"]
    return sorted((e for e in rows if e["action"] == action), key=lambda e: e["event_id"])


# --- the role setting ---------------------------------------------------------------------------

def test_roles_store_replace_on_burn_and_it_is_off_by_default(env):
    assert make_role(env, ROLE)["replace_on_burn"] is True
    assert env.ddb.Table("burn-roles").get_item(Key={"name": "crawler"})["Item"]["replace_on_burn"] is True
    assert make_role(env, {"name": "plain", "tools": []})["replace_on_burn"] is False
    status, saved = call(env, "PUT", "/roles/crawler", ROLE | {"replace_on_burn": False})
    assert status == 200 and saved["replace_on_burn"] is False
    assert call(env, "POST", "/roles", {"name": "x", "tools": [], "replace_on_burn": "yes"})[0] == 400
    env.ddb.Table("burn-roles").put_item(Item={"name": "old", "tools": [], "managed_by": "ui"})
    assert call(env, "GET", "/roles/old")[1]["replace_on_burn"] is False  # rows from before the setting
    roles = {r["name"]: r for r in call(env, "GET", "/roles")[1]["roles"]}
    assert roles["a"]["replace_on_burn"] is False  # scenario roles never replace


# --- reads ---------------------------------------------------------------------------------------

def test_instance_reads_show_links_and_progress_counts_never_the_map(env):
    make_role(env, ROLE)
    sid = fresh(env)["session_id"]
    status, one = call(env, "GET", f"/instances/{sid}")
    assert status == 200 and one["session_id"] == sid
    assert (one["generation"], one["replaces"], one["replaced_by"], one["replacement"]) == (0, None, None, None)
    assert (one["items_started"], one["items_done"]) == (0, 0)
    env.ddb.Table("burn-sessions").update_item(
        Key={"session_id": sid}, UpdateExpression="SET progress = :p",
        ExpressionAttributeValues={":p": {"a": {"started_at": "t1", "done_at": "t2"},
                                          "b": {"started_at": "t1", "done_at": None}}})
    listed = next(i for i in call(env, "GET", "/instances")[1]["instances"] if i["session_id"] == sid)
    assert (listed["items_started"], listed["items_done"]) == (2, 1) and "progress" not in listed
    assert call(env, "GET", "/instances/sess_nope")[0] == 404
    assert call(env, "GET", f"/instances/{sid}", token="")[0] == 401  # behind the admin header (a person, later)


# --- progress on the heartbeat -------------------------------------------------------------------

def test_heartbeat_progress_is_stamped_with_burns_clock(env):
    make_role(env, ROLE)
    sid = fresh(env)["session_id"]
    status, view = beat(env, sid, ("a", "started"), ("b", "done"), ("a", "started"))
    assert status == 200 and (view["items_started"], view["items_done"]) == (2, 1)
    stored = session_row(env, sid)["progress"]
    assert stored["b"]["started_at"] == stored["b"]["done_at"] == view["last_seen"]  # Burn's receive time
    first = stored["a"]["started_at"]
    status, view = beat(env, sid, ("a", "started"), ("a", "done"), ("b", "done"))
    stored = session_row(env, sid)["progress"]
    assert stored["a"]["started_at"] == first and stored["a"]["done_at"] == view["last_seen"]
    assert stored["b"]["done_at"] == first  # a repeat never moves a time
    assert beat(env, sid, raw={})[0] == 200 and beat(env, sid, raw={"progress": []})[0] == 200
    assert session_row(env, sid)["progress"] == stored


@pytest.mark.parametrize("raw, rule", [
    ({"progress": [{"item": "has space", "state": "done"}]}, "item id"),
    ({"progress": [{"item": "a", "state": "finished"}]}, "state"),
    ({"progress": [{"item": "a"}]}, "item and state"),
    ({"progress": {"item": "a", "state": "done"}}, "list"),
    ({"progress": [{"item": f"i{n}", "state": "started"} for n in range(names.MAX_PROGRESS_EVENTS + 1)]},
     f"at most {names.MAX_PROGRESS_EVENTS}"),
    ({"progress": [], "note": "hello"}, "only progress"),
])
def test_a_broken_progress_rule_is_a_400_that_records_nothing(env, raw, rule):
    make_role(env, ROLE)
    sid = fresh(env)["session_id"]
    before = session_row(env, sid)
    status, body = beat(env, sid, raw=raw)
    assert status == 400 and rule in body["error"]
    after = session_row(env, sid)
    assert after.get("progress") == before.get("progress") and after["last_seen"] == before["last_seen"]


def test_at_most_500_items_per_instance(env):
    make_role(env, ROLE)
    sid = fresh(env)["session_id"]
    step = names.MAX_PROGRESS_EVENTS
    for start in range(0, names.MAX_PROGRESS_ITEMS, step):
        assert beat(env, sid, *[(f"i{n}", "started") for n in range(start, start + step)])[0] == 200
    assert beat(env, sid, ("i0", "done"))[0] == 200  # a known item is fine
    status, body = beat(env, sid, ("i1", "done"), ("one-too-many", "started"))
    assert status == 400 and str(names.MAX_PROGRESS_ITEMS) in body["error"]
    row = session_row(env, sid)
    assert len(row["progress"]) == names.MAX_PROGRESS_ITEMS and row["progress"]["i1"]["done_at"] is None


def test_progress_from_an_ended_instance_is_a_409_and_never_stored(env):
    make_role(env, ROLE)
    sid = fresh(env)["session_id"]
    call(env, "POST", "/burns", {"scope": "role", "id": "crawler"})
    status, body = beat(env, sid, ("a", "done"))
    assert status == 409 and "revoked" in body["error"]
    assert "a" not in (session_row(env, sid).get("progress") or {})


def test_progress_rides_on_the_instances_own_token(env):
    make_role(env, ROLE)
    joined = fresh(env)
    status, view = as_instance(env, "POST", f"/instances/{joined['session_id']}/heartbeat",
                               joined["secrets"]["session_token"], {"progress": [{"item": "a", "state": "done"}]})
    assert status == 200 and view["items_done"] == 1


def test_a_heartbeat_that_races_another_keeps_both(env, monkeypatch):
    """Optimistic: a heartbeat whose read went stale retries on fresh state instead of overwriting it."""
    import instances
    make_role(env, ROLE)
    sid = fresh(env)["session_id"]
    real_get, raced = instances.get_session, []

    def stale_read(session_id, consistent=False):
        row = real_get(session_id, consistent)
        if not raced:
            raced.append(True)
            assert beat(env, sid, ("other", "done"))[0] == 200  # lands between this read and its write
        return row

    monkeypatch.setattr(instances, "get_session", stale_read)
    assert beat(env, sid, ("mine", "done"))[0] == 200
    assert set(session_row(env, sid)["progress"]) == {"other", "mine"}


@pytest.mark.parametrize("raw", [
    {"progress": [{"item": "has space", "state": "done"}]},
    {"progress": "garbage"},
    {"progress": [], "note": "hello"},
])
def test_a_burned_instance_always_learns_it_is_burned_even_with_bad_progress(env, raw):
    make_role(env, ROLE)
    sid = fresh(env)["session_id"]
    call(env, "POST", "/burns", {"scope": "instance", "id": sid})
    status, body = beat(env, sid, raw=raw)
    assert status == 409 and "revoked" in body["error"]
