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


# --- linking a replacement -------------------------------------------------------------------------

def test_a_pending_replacement_is_linked_once_in_one_transaction(env):
    make_role(env, ROLE)
    old = fresh(env)
    mark_burned(env, old["session_id"], done=["w01-item-01"], quarantined=["w01-item-02"])
    backdate(env, old["session_id"])
    new = fresh(env)
    status, linked = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": new["session_id"]})
    assert status == 200, linked
    assert linked == {"replaces": old["session_id"], "session_id": new["session_id"], "generation": 1,
                      "cutoff": "2026-11-07T10:00:00.000Z", "cutoff_source": "burn_time",
                      "done": ["w01-item-01"], "quarantined": ["w01-item-02"]}
    old_view = call(env, "GET", f"/instances/{old['session_id']}")[1]
    new_view = call(env, "GET", f"/instances/{new['session_id']}")[1]
    assert old_view["replacement"]["status"] == "linked" and old_view["replaced_by"] == new["session_id"]
    assert new_view["replaces"] == old["session_id"] and new_view["generation"] == 1
    again = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": fresh(env)["session_id"]})
    assert again[0] == 409 and "linked" in again[1]["error"]

    replaced, resumed = events(env, "replaced"), events(env, "resumed")
    assert [(e["agent_id"], e["session_id"], e["replaced_by"], e["detail"]) for e in replaced] == [
        (old["agent_id"], old["session_id"], new["session_id"], f"replaced by {new['agent_id']}")]
    assert [(e["agent_id"], e["session_id"], e["replaces"], e["detail"]) for e in resumed] == [
        (new["agent_id"], new["session_id"], old["session_id"], "1 done, 1 quarantined")]
    assert {e["source"] for e in replaced + resumed} == {"instance"}
    assert {e["role"] for e in replaced + resumed} == {"crawler"}


def test_the_generation_counts_up_from_the_burned_instance(env):
    make_role(env, ROLE)
    old = fresh(env)
    mark_burned(env, old["session_id"], generation=2)
    backdate(env, old["session_id"])
    new = fresh(env)
    status, linked = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": new["session_id"]})
    assert status == 200 and linked["generation"] == 3
    assert call(env, "GET", f"/instances/{new['session_id']}")[1]["generation"] == 3


@pytest.mark.parametrize("state", ["linked", "needs_person", None, "never-burned"])
def test_only_a_pending_replacement_can_be_linked(env, state):
    make_role(env, ROLE)
    old = fresh(env)
    if state == "never-burned":
        pass
    elif state is None:
        call(env, "POST", "/burns", {"scope": "instance", "id": old["session_id"]})  # no record yet at this layer
        env.ddb.Table("burn-sessions").update_item(Key={"session_id": old["session_id"]},
                                                   UpdateExpression="REMOVE replacement")
    else:
        mark_burned(env, old["session_id"], status=state)
    backdate(env, old["session_id"])
    new = fresh(env)
    status, body = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": new["session_id"]})
    assert status == 409 and "pending" in body["error"]
    assert session_row(env, new["session_id"]).get("replaces") is None


def test_the_new_instance_must_be_a_fresh_active_instance_of_the_same_role(env):
    make_role(env, ROLE)
    make_role(env, ROLE | {"name": "other"})
    early = fresh(env)
    old = fresh(env)
    mark_burned(env, old["session_id"])  # burned at 10:00; early joined before that
    backdate(env, early["session_id"], "2026-11-07T09:59:00.000Z")
    backdate(env, old["session_id"], "2026-11-07T09:58:00.000Z")
    gone = fresh(env)
    call(env, "POST", f"/instances/{gone['session_id']}/exit")
    other = fresh(env, "other")
    cases = {early["session_id"]: "after", old["session_id"]: "burned one", gone["session_id"]: "active",
             other["session_id"]: "same role", "sess_missing": "does not exist"}
    for new_id, rule in cases.items():
        status, body = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": new_id})
        assert status == 400 and rule in body["error"], (new_id, body)
    for bad in ({}, {"session_id": 5}, {"session_id": "a b"}, {"session_id": "x", "extra": 1}):
        assert call(env, "POST", f"/instances/{old['session_id']}/replace", bad)[0] == 400
    second = fresh(env)
    other_old = fresh(env)
    mark_burned(env, other_old["session_id"])
    backdate(env, other_old["session_id"], "2026-11-07T09:58:00.000Z")
    assert call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": second["session_id"]})[0] == 200
    status, body = call(env, "POST", f"/instances/{other_old['session_id']}/replace",
                        {"session_id": second["session_id"]})
    assert status == 400 and "already" in body["error"]
    assert call(env, "POST", "/instances/sess_missing/replace", {"session_id": second["session_id"]})[0] == 404
    assert session_row(env, old["session_id"])["replacement"]["status"] == "linked"
    assert session_row(env, other_old["session_id"])["replacement"]["status"] == "pending"


def test_linking_needs_a_person_never_an_instance_token(env):
    make_role(env, ROLE)
    old = fresh(env)
    mark_burned(env, old["session_id"])
    backdate(env, old["session_id"])
    new = fresh(env)
    for token in (new["secrets"]["session_token"], old["secrets"]["session_token"]):
        status, _ = as_instance(env, "POST", f"/instances/{old['session_id']}/replace", token,
                                {"session_id": new["session_id"]})
        assert status == 401
    assert call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": new["session_id"]},
                token="")[0] == 401
    assert session_row(env, old["session_id"])["replacement"]["status"] == "pending"


def test_two_launchers_racing_link_exactly_one_replacement(env):
    make_role(env, ROLE)
    old = fresh(env)
    mark_burned(env, old["session_id"])
    backdate(env, old["session_id"])
    candidates = [fresh(env)["session_id"] for _ in range(6)]
    results, barrier = {}, threading.Barrier(len(candidates))

    def link(new_id):
        barrier.wait()
        results[new_id] = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": new_id})

    threads = [threading.Thread(target=link, args=(c,)) for c in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [c for c, (status, _) in results.items() if status == 200]
    assert len(winners) == 1, results
    assert {status for c, (status, _) in results.items() if c not in winners} == {409}
    assert session_row(env, old["session_id"])["replaced_by"] == winners[0]
    assert [c for c in candidates if session_row(env, c).get("replaces")] == winners
    assert len(events(env, "replaced")) == len(events(env, "resumed")) == 1


def test_a_link_that_loses_the_race_after_its_checks_is_refused_by_the_transaction(env, monkeypatch):
    """Both callers read `pending`; the transaction, not the read, decides."""
    import instances
    make_role(env, ROLE)
    old = fresh(env)
    mark_burned(env, old["session_id"])
    backdate(env, old["session_id"])
    first, second = fresh(env)["session_id"], fresh(env)["session_id"]
    real_get, raced = instances.get_session, []

    def stale(session_id, consistent=False):
        row = real_get(session_id, consistent)
        if session_id == old["session_id"] and not raced:
            raced.append(True)
            assert call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": first})[0] == 200
        return row

    monkeypatch.setattr(instances, "get_session", stale)
    status, body = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": second})
    assert status == 409, body
    assert session_row(env, old["session_id"])["replaced_by"] == first
    assert session_row(env, second).get("replaces") is None and session_row(env, second).get("generation") is None


def test_a_failed_event_write_does_not_undo_the_link(env, monkeypatch):
    import db
    make_role(env, ROLE)
    old = fresh(env)
    mark_burned(env, old["session_id"])
    backdate(env, old["session_id"])
    new = fresh(env)
    monkeypatch.setattr(db, "write_event", lambda **_: (_ for _ in ()).throw(RuntimeError("events down")))
    status, linked = call(env, "POST", f"/instances/{old['session_id']}/replace", {"session_id": new["session_id"]})
    assert status == 200 and linked["generation"] == 1
    assert session_row(env, old["session_id"])["replaced_by"] == new["session_id"]
