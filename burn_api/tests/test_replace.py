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
