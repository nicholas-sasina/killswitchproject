"""Roles, join tokens, instances and scoped burns, checked against the Burn API contract."""

import json

import jsonschema
from burn_contracts.names import JOIN_TOKEN_RE, is_session_token, scenario_role_name

from mock_data import CONTRACTS
from test_burn_api import check, start

PLANNER = {"name": "planner", "owner": "platform", "tools": ["list_tickets", "read_ticket", "send_message"],
           "credentials": ["api_key"], "may_message": ["worker"], "may_spawn": ["worker"]}
WORKER = {"name": "worker", "tools": ["read_ticket", "notify"], "credentials": ["api_key", "aws_key"],
          "egress": ["bedrock-runtime.us-east-1.amazonaws.com"]}


def make_role(client, spec):
    return check(client.post("/roles", json=spec), "/roles", "post", 201)


def join(client, role, token):
    return check(client.post("/sessions", json={"role": role, "enroll_token": token}), "/sessions", "post", 201)


def token_for(client, role, **body):
    return check(client.post(f"/roles/{role}/tokens", json=body), "/roles/{name}/tokens", "post", 201)


def test_scenario_agents_are_read_only_singleton_roles(client, scenario):
    agent_id = scenario[1]["agents"][0]["id"]
    start(client, agent_id)
    roles = {r["name"]: r for r in check(client.get("/roles"), "/roles")["roles"]}
    role = roles[scenario_role_name(agent_id)]
    assert role["managed_by"] == "scenario" and role["active_instances"] == 1
    assert client.put(f"/roles/{role['name']}", json={"name": role["name"], "tools": []}).status_code == 409
    instances = check(client.get("/instances", params={"role": role["name"]}), "/instances")["instances"]
    assert [i["agent_id"] for i in instances] == [agent_id]


def test_join_token_is_shown_once_and_limits_uses(client):
    make_role(client, WORKER)
    created = token_for(client, "worker", uses=2, ttl=600)
    assert JOIN_TOKEN_RE.match(created["token"]) and created["uses_left"] == 2
    first, second = join(client, "worker", created["token"]), join(client, "worker", created["token"])
    assert first["agent_id"] != second["agent_id"] and first["agent_id"].startswith("agent-worker-")
    assert first["token_id"] == created["token_id"] and first["egress"] == WORKER["egress"]
    assert client.post("/sessions", json={"role": "worker", "enroll_token": created["token"]}).status_code == 403
    assert created["token"] not in json.dumps(client.app.state.swarm.tokens)
    assert created["token"] not in json.dumps(client.app.state.mock.events)
    wrong_secret = created["token"][:-4] + "AAAA"
    assert client.post("/sessions", json={"role": "worker", "enroll_token": wrong_secret}).status_code == 403
    revoked = check(client.delete(f"/tokens/{created['token_id']}"), "/tokens/{token_id}", "delete")
    assert revoked["status"] == "revoked"


def test_child_needs_may_spawn_and_never_gets_more_than_its_parent(client):
    make_role(client, PLANNER)
    make_role(client, WORKER)
    parent = join(client, "planner", token_for(client, "planner")["token"])
    child = check(client.post("/sessions", json={"role": "worker", "parent_session": parent["session_id"]}),
                  "/sessions", "post", 201)
    assert child["parent_session"] == parent["session_id"]
    assert child["tools"] == ["read_ticket"]  # notify is not in the planner's tools
    assert [c["type"] for c in child["credentials"]] == ["api_key"]  # the planner has no aws_key
    worker = join(client, "worker", token_for(client, "worker")["token"])
    refused = client.post("/sessions", json={"role": "planner", "parent_session": worker["session_id"]})
    assert refused.status_code == 403


def test_heartbeat_exit_and_scoped_burns(client):
    make_role(client, PLANNER)
    make_role(client, WORKER)
    root = join(client, "planner", token_for(client, "planner")["token"])
    kids = [check(client.post("/sessions", json={"role": "worker", "parent_session": root["session_id"]}),
                  "/sessions", "post", 201) for _ in range(2)]
    grandchild_refused = client.post("/sessions", json={"role": "worker", "parent_session": kids[0]["session_id"]})
    assert grandchild_refused.status_code == 403  # workers may not spawn
    token = token_for(client, "worker", uses=3)
    joined = [join(client, "worker", token["token"]) for _ in range(3)]

    beat = check(client.post(f"/instances/{joined[0]['session_id']}/heartbeat"),
                 "/instances/{session_id}/heartbeat", "post")
    assert beat["status"] == "active" and beat["last_seen"]
    gone = check(client.post(f"/instances/{joined[0]['session_id']}/exit"), "/instances/{session_id}/exit", "post")
    assert gone["status"] == "exited" and gone["exited_at"]
    assert client.post(f"/instances/{joined[0]['session_id']}/heartbeat").status_code == 409
    assert check(client.get(f"/badge/{joined[0]['session_id']}"), "/badge/{session_id}")["status"] == "exited"

    one = check(client.post("/burns", json={"scope": "instance", "id": joined[1]["session_id"]}), "/burns", "post")
    assert [b["agent_id"] for b in one["burned"]] == [joined[1]["agent_id"]]

    lineage = check(client.post("/burns", json={"scope": "lineage", "id": root["session_id"]}), "/burns", "post")
    assert {b["agent_id"] for b in lineage["burned"]} == {root["agent_id"], *(k["agent_id"] for k in kids)}

    by_token = check(client.post("/burns", json={"scope": "token", "id": token["token_id"]}), "/burns", "post")
    assert [b["agent_id"] for b in by_token["burned"]] == [joined[2]["agent_id"]]  # exited and burned are skipped
    assert client.app.state.swarm.token_view(token["token_id"])["status"] == "revoked"

    fresh = join(client, "worker", token_for(client, "worker")["token"])
    by_role = check(client.post("/burns", json={"scope": "role", "id": "worker"}), "/burns", "post")
    assert [b["agent_id"] for b in by_role["burned"]] == [fresh["agent_id"]]

    listed = check(client.get("/instances", params={"role": "worker"}), "/instances")["instances"]
    assert {i["status"] for i in listed} == {"revoked", "exited"}
    assert check(client.get("/instances", params={"status": "active"}), "/instances")["instances"] == []
    assert client.post("/burns", json={"scope": "instance", "id": "missing"}).status_code == 404

    event_schema = json.loads((CONTRACTS / "ledger-event.schema.json").read_text())
    for event in client.app.state.mock.events:
        jsonschema.validate(event, event_schema)
    actions = {(e["source"], e["action"]) for e in client.app.state.mock.events}
    assert {("instance", "started"), ("instance", "exited"), ("burn", "rotated")} <= actions


def test_role_ownership_rules(client):
    make_role(client, WORKER | {"managed_by": "code"})
    assert client.post("/roles", json=WORKER).status_code == 409
    ui_edit = client.put("/roles/worker", json=WORKER | {"managed_by": "ui"})
    assert ui_edit.status_code == 409
    saved = check(client.put("/roles/worker", json=WORKER | {"managed_by": "code", "owner": "ml-team"}),
                  "/roles/{name}", "put")
    assert saved["owner"] == "ml-team"
    assert check(client.get("/roles/worker"), "/roles/{name}")["managed_by"] == "code"
    assert client.get("/roles/nope").status_code == 404
    assert client.post("/roles", json={"name": "Bad Name", "tools": []}).status_code == 422


def test_a_role_instance_gets_its_secret_values_once_and_a_scenario_agent_does_not(client, scenario):
    make_role(client, WORKER)
    make_role(client, PLANNER | {"may_spawn": ["worker"]})
    joined = join(client, "worker", token_for(client, "worker")["token"])
    assert set(joined["secrets"]) == {"badge", "api_key", "aws_key", "session_token"}
    assert is_session_token(joined["secrets"]["session_token"])
    assert set(joined["secrets"]["badge"]) == {"client_id", "client_secret", "token_url", "scope"}
    assert set(joined["secrets"]["aws_key"]) == {"access_key_id", "secret_access_key"}
    parent = join(client, "planner", token_for(client, "planner")["token"])
    assert set(parent["secrets"]) == {"badge", "api_key", "session_token"}  # only what the role mints
    child = check(client.post("/sessions", json={"role": "worker", "parent_session": parent["session_id"]}),
                  "/sessions", "post", 201)
    assert "badge" in child["secrets"] and "aws_key" not in child["secrets"]  # narrowed to the parent's
    assert child["secrets"]["session_token"] != parent["secrets"]["session_token"]
    instance = check(client.get("/instances"), "/instances")["instances"][0]
    assert "secrets" not in json.dumps(instance)
    assert "secrets" not in start(client, scenario[1]["agents"][0]["id"])


def test_eval_role_names_are_reserved(client):
    for name in ("eval", "eval-7"):
        response = client.post("/roles", json={"name": name, "tools": []})
        assert response.status_code == 400 and "reserved" in response.json()["detail"]
        assert client.put(f"/roles/{name}", json={"name": name, "tools": []}).status_code == 400
    assert client.post("/roles", json={"name": "evaluator", "tools": []}).status_code == 201


def test_the_badge_in_a_join_reply_gets_a_token_the_mock_mcp_server_accepts(client):
    import base64
    make_role(client, WORKER)
    token = token_for(client, "worker")["token"]
    reply = join(client, "worker", token)
    badge = reply["secrets"]["badge"]
    assert badge["client_id"] == reply["agent_id"] and badge["token_url"].endswith("/mock-token")
    basic = base64.b64encode(f"{badge['client_id']}:{badge['client_secret']}".encode()).decode()
    issued = client.post("/mock-token", headers={"Authorization": f"Basic {basic}"},
                         data={"grant_type": "client_credentials"})
    assert issued.status_code == 200 and issued.json()["access_token"] == "mock-" + reply["agent_id"]
    assert client.post("/mock-token").status_code == 401


def test_post_instances_starts_the_same_role_instances_as_post_sessions(client):
    make_role(client, WORKER)
    make_role(client, PLANNER | {"may_spawn": ["worker"]})

    def started(body):
        return check(client.post("/instances", json=body), "/instances", "post", 201)

    joined = started({"role": "worker", "enroll_token": token_for(client, "worker")["token"]})
    assert set(joined["secrets"]) == {"badge", "session_token", "api_key", "aws_key"}
    assert joined["agent_id"].startswith("agent-worker-")
    parent = started({"role": "planner", "enroll_token": token_for(client, "planner")["token"]})
    child = started({"role": "worker", "parent_session": parent["session_id"]})
    assert child["parent_session"] == parent["session_id"] and "aws_key" not in child["secrets"]  # narrowed
    # the same refusals as the role forms of /sessions
    assert client.post("/instances", json={"role": "worker", "enroll_token": "bjt_bad"}).status_code == 403
    assert client.post("/instances", json={"role": "planner", "parent_session": child["session_id"]}).status_code == 403
    # only the two role forms
    assert client.post("/instances", json={"agent_id": "agent-reader"}).status_code == 400
    assert client.post("/instances", json={"role": "worker"}).status_code == 400
    assert client.post("/instances", json={"role": "worker", "enroll_token": "x", "parent_session": "y"}).status_code == 400
