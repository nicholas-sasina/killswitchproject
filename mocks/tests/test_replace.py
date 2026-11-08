"""Replace on burn in the development API: progress on the heartbeat, the record a burn writes, and the link."""

import json

import jsonschema
from burn_contracts import names
from fastapi.testclient import TestClient

from mock_data import CONTRACTS
from test_swarm import check, join, make_role, token_for

ROLE = {"name": "crawler", "tools": ["read_inbox"], "replace_on_burn": True}


def beat(client, session_id, *events, status=200):
    response = client.post(f"/instances/{session_id}/heartbeat",
                           json={"progress": [{"item": item, "state": state} for item, state in events]})
    if status == 200:
        return check(response, "/instances/{session_id}/heartbeat", "post")
    assert response.status_code == status, response.text
    return response.json()


def instance(client, session_id):
    return check(client.get(f"/instances/{session_id}"), "/instances/{session_id}")


def burn_one(client, session_id):
    return check(client.post("/burns", json={"scope": "instance", "id": session_id}), "/burns", "post")


def replace(client, old, new, status=200):
    response = client.post(f"/instances/{old}/replace", json={"session_id": new})
    if status == 200:
        return check(response, "/instances/{session_id}/replace", "post")
    assert response.status_code == status, response.text
    return response.json()


def fresh(client, role="crawler"):
    return join(client, role, token_for(client, role)["token"])


def test_roles_carry_replace_on_burn_and_it_is_off_by_default(client):
    assert make_role(client, ROLE)["replace_on_burn"] is True
    assert make_role(client, ROLE | {"name": "plain"})["replace_on_burn"] is True
    assert make_role(client, {"name": "other", "tools": []})["replace_on_burn"] is False
    scenario_roles = [r for r in check(client.get("/roles"), "/roles")["roles"] if r["managed_by"] == "scenario"]
    assert scenario_roles and not any(r["replace_on_burn"] for r in scenario_roles)


def test_heartbeat_progress_is_stamped_by_burn_and_shown_as_counts(client):
    make_role(client, ROLE)
    one = fresh(client)
    sid = one["session_id"]
    assert check(client.post(f"/instances/{sid}/heartbeat"), "/instances/{session_id}/heartbeat", "post")[
        "items_started"] == 0  # a bare heartbeat still works
    view = beat(client, sid, ("a", "started"), ("b", "done"), ("a", "started"))
    assert (view["items_started"], view["items_done"]) == (2, 1)
    assert "progress" not in view and view["generation"] == 0 and view["replacement"] is None
    stored = client.app.state.mock.sessions[sid]["progress"]
    assert stored["b"]["started_at"] == stored["b"]["done_at"]  # done without started: both at once
    first_start = stored["a"]["started_at"]
    beat(client, sid, ("a", "started"), ("a", "done"))
    assert client.app.state.mock.sessions[sid]["progress"]["a"]["started_at"] == first_start
    assert instance(client, sid)["items_done"] == 2
    assert client.get("/instances/missing").status_code == 404


def test_a_broken_progress_rule_is_a_400_and_records_nothing(client):
    make_role(client, ROLE)
    sid = fresh(client)["session_id"]
    for events in ([("has space", "done")], [("a", "finished")],
                   [(f"i{n}", "started") for n in range(names.MAX_PROGRESS_EVENTS + 1)]):
        assert "detail" in beat(client, sid, *events, status=400)
    assert client.post(f"/instances/{sid}/heartbeat", json={"progress": [], "note": "hi"}).status_code == 400
    for start in range(0, names.MAX_PROGRESS_ITEMS, names.MAX_PROGRESS_EVENTS):
        beat(client, sid, *[(f"i{n}", "started") for n in range(start, start + names.MAX_PROGRESS_EVENTS)])
    refused = beat(client, sid, ("i0", "done"), ("one-too-many", "started"), status=400)
    assert str(names.MAX_PROGRESS_ITEMS) in refused["detail"]
    view = instance(client, sid)
    assert (view["status"], view["items_started"], view["items_done"]) == ("active", names.MAX_PROGRESS_ITEMS, 0)


def test_an_instance_burn_leaves_a_pending_replacement_that_a_launcher_links_once(client):
    make_role(client, ROLE)
    old = fresh(client)
    beat(client, old["session_id"], ("w01-item-01", "started"), ("w01-item-01", "done"), ("w01-item-02", "started"))
    burn_one(client, old["session_id"])
    record = instance(client, old["session_id"])["replacement"]
    assert record["status"] == "pending" and record["cutoff_source"] == "burn_time"
    assert (record["done"], record["quarantined"]) == (["w01-item-01"], ["w01-item-02"])
    burn_one(client, old["session_id"])  # burning it again changes nothing
    assert instance(client, old["session_id"])["replacement"] == record

    new = fresh(client)
    linked = replace(client, old["session_id"], new["session_id"])
    assert linked == {"replaces": old["session_id"], "session_id": new["session_id"], "generation": 1,
                      "cutoff": record["cutoff"], "cutoff_source": "burn_time",
                      "done": ["w01-item-01"], "quarantined": ["w01-item-02"]}
    old_view, new_view = instance(client, old["session_id"]), instance(client, new["session_id"])
    assert old_view["replacement"]["status"] == "linked" and old_view["replaced_by"] == new["session_id"]
    assert new_view["replaces"] == old["session_id"] and new_view["generation"] == 1
    assert "pending" in replace(client, old["session_id"], fresh(client)["session_id"], status=409)["detail"]

    events = client.app.state.mock.events
    event_schema = json.loads((CONTRACTS / "ledger-event.schema.json").read_text())
    for event in events:
        jsonschema.validate(event, event_schema)
    replaced = [e for e in events if e["action"] == "replaced"]
    resumed = [e for e in events if e["action"] == "resumed"]
    assert [(e["agent_id"], e["replaced_by"], e["detail"]) for e in replaced] == [
        (old["agent_id"], new["session_id"], f"replaced by {new['agent_id']}")]
    assert [(e["agent_id"], e["replaces"], e["detail"]) for e in resumed] == [
        (new["agent_id"], old["session_id"], "1 done, 1 quarantined")]
    assert {e["source"] for e in replaced + resumed} == {"instance"} and replaced[0]["actor"] == "admin"


def test_the_replacement_must_be_a_fresh_active_instance_of_the_same_role(client):
    make_role(client, ROLE)
    make_role(client, ROLE | {"name": "other"})
    early = fresh(client)  # joined before the burn (backdated, so a shared millisecond cannot blur it)
    client.app.state.mock.sessions[early["session_id"]]["created_at"] = "2020-01-01T00:00:00.000Z"
    old = fresh(client)
    burn_one(client, old["session_id"])
    gone = fresh(client)
    check(client.post(f"/instances/{gone['session_id']}/exit"), "/instances/{session_id}/exit", "post")
    other = fresh(client, "other")
    for new in (early["session_id"], old["session_id"], gone["session_id"], other["session_id"], "missing"):
        assert "detail" in replace(client, old["session_id"], new, status=400), new
    second_old = fresh(client)
    burn_one(client, second_old["session_id"])
    first = fresh(client)  # created after both burns, so only "already a replacement" can refuse it
    replace(client, old["session_id"], first["session_id"])
    assert "already" in replace(client, second_old["session_id"], first["session_id"], status=400)["detail"]
    assert client.post("/instances/missing/replace", json={"session_id": first["session_id"]}).status_code == 404
    assert instance(client, old["session_id"])["replaced_by"] == first["session_id"]


def test_no_replacement_without_the_setting_for_a_role_burn_or_for_a_running_instance(client):
    make_role(client, ROLE)
    make_role(client, ROLE | {"name": "plain", "replace_on_burn": False})
    running = fresh(client)
    assert replace(client, running["session_id"], fresh(client)["session_id"], status=409)
    plain = fresh(client, "plain")
    burn_one(client, plain["session_id"])
    assert instance(client, plain["session_id"])["replacement"] is None
    check(client.post("/burns", json={"scope": "role", "id": "crawler"}), "/burns", "post")
    listed = check(client.get("/instances", params={"role": "crawler"}), "/instances")["instances"]
    assert listed and all(i["status"] == "revoked" and i["replacement"] is None for i in listed)


def test_a_task_is_replaced_at_most_three_times_then_it_needs_a_person(client):
    make_role(client, ROLE)
    current = fresh(client)
    for generation in range(1, names.MAX_REPLACEMENT_GENERATION + 1):
        burn_one(client, current["session_id"])
        new = fresh(client)
        assert replace(client, current["session_id"], new["session_id"])["generation"] == generation
        current = new
    burn_one(client, current["session_id"])
    record = instance(client, current["session_id"])["replacement"]
    assert record["status"] == "needs_person"
    assert replace(client, current["session_id"], fresh(client)["session_id"], status=409)


def test_linking_a_replacement_needs_a_person_not_an_instance(client):
    make_role(client, ROLE)
    old = fresh(client)
    burn_one(client, old["session_id"])
    new = fresh(client)
    bearer = {"authorization": f"Bearer {new['secrets']['session_token']}"}
    with TestClient(client.app) as anonymous:  # no admin header: only the instance's own token
        refused = anonymous.post(f"/instances/{old['session_id']}/replace",
                                 json={"session_id": new["session_id"]}, headers=bearer)
        assert refused.status_code == 401
        assert anonymous.get(f"/instances/{new['session_id']}", headers=bearer).status_code == 401
    assert instance(client, old["session_id"])["replacement"]["status"] == "pending"
