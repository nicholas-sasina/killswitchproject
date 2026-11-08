import asyncio
import json
import re
import secrets

import jsonschema
from burn_contracts.names import MOCK_HEADER, agent_secret_ref, shared_secret_ref
from fastapi.testclient import TestClient

from burn_api_mock import create_app
from conftest import response_schema
from mock_data import CONTRACTS


def check(response, path, method="get", status=200, contract="burn-api.openapi.yaml"):
    assert response.status_code == status
    assert response.headers[MOCK_HEADER] == "true"
    body = response.json()
    jsonschema.validate(body, response_schema(contract, path, method, status))
    return body


def start(client, agent_id):
    return check(client.post("/sessions", json={"agent_id": agent_id},
                             headers={"x-burn-demo-token": secrets.token_hex(12)}),
                 "/sessions", "post", 201)


def test_sessions_activity_burn_and_other_agent_survives(client, scenario):
    agents = [agent["id"] for agent in scenario[1]["agents"]]
    initial = check(client.get("/events"), "/events")
    assert all(agent["status"] == "idle" for agent in initial["agents"])
    first = start(client, agents[0])
    second_session = start(client, agents[0])
    other = start(client, agents[1])
    assert first["session_id"] != second_session["session_id"]
    assert first["credentials"] == second_session["credentials"]
    assert check(client.get(f"/badge/{first['session_id']}"), "/badge/{session_id}")["status"] == "active"
    for _ in range(3):
        client.app.state.mock.tick()
    ledger = check(client.get(f"/agents/{agents[0]}/ledger"), "/agents/{agent_id}/ledger")
    assert all(re.fullmatch(r"[a-f0-9]{64}", row["fingerprint"]) for row in ledger["entries"])
    assert all(row["flag_level"] is None for row in ledger["entries"])
    before = check(client.get("/events"), "/events")
    assert {"recorded", "inherited", "blocked"} <= {event["action"] for event in before["events"]}
    result = check(client.post("/burn", json={"agent_id": agents[0]}), "/burn", "post")
    expected = {agent_secret_ref(agents[0], kind) for kind in scenario[1]["agents"][0]["credentials"]}
    assert set(result["rotated"]) == expected
    assert set(result["sessions_revoked"]) == {first["session_id"], second_session["session_id"]}
    assert shared_secret_ref(scenario[1]["shared_credentials"][0]["name"]) in result["flagged"]
    assert check(client.get(f"/badge/{first['session_id']}"), "/badge/{session_id}")["status"] == "revoked"
    assert check(client.get(f"/badge/{other['session_id']}"), "/badge/{session_id}")["status"] == "active"
    client.app.state.mock.tick()
    after = check(client.get("/events", params={"since": before["cursor"]}), "/events")
    assert {"revoked", "rotated", "flagged", "refused"} <= {event["action"] for event in after["events"]}
    assert all(event["source"] == "gateway" for event in after["events"]
               if event["agent_id"] == agents[0] and event["action"] == "refused")
    assert any(event["agent_id"] == agents[1] and event["action"] == "recorded" for event in after["events"])
    entries = check(client.get(f"/agents/{agents[0]}/ledger"), "/agents/{agent_id}/ledger")["entries"]
    assert {entry["status"] for entry in entries} == {"rotated", "flagged"}
    assert {entry["flag_level"] for entry in entries if entry["status"] == "flagged"} == {"seen"}
    inherited = check(client.get(f"/agents/{agents[1]}/ledger"), "/agents/{agent_id}/ledger")["entries"]
    assert any(entry["owner_agent_id"] == agents[0] and entry["status"] == "rotated" for entry in inherited)
    blast = check(client.get(f"/burn/{agents[0]}/blast-radius"), "/burn/{agent_id}/blast-radius")
    assert {event["event_name"] for event in blast["events"]} == {"ListBuckets", "GetCallerIdentity", "InvokeModel"}
    assert all(event["key_ref"].endswith("#v1") for event in blast["events"])
    assert [key["status"] for key in blast["keys"]] == ["inactive", "active"]
    event_schema = json.loads((CONTRACTS / "ledger-event.schema.json").read_text())
    for event in client.app.state.mock.events:
        jsonschema.validate(event, event_schema)


def test_burn_flags_other_agents_credentials(client, scenario):
    first, second = [agent["id"] for agent in scenario[1]["agents"]]
    start(client, first)
    start(client, second)
    client.app.state.mock.tick()
    result = check(client.post("/burn", json={"agent_id": second}), "/burn", "post")
    assert agent_secret_ref(first, "aws_key") in result["flagged"]
    assert all(ref.startswith(f"burn/{second}/") for ref in result["rotated"])


def test_leak_modes_encodings_and_no_payload_echo(client, scenario):
    agent_id = scenario[1]["agents"][0]["id"]
    start(client, agent_id)
    client.app.state.mock.tick()
    encodings = check(client.get("/encodings"), "/encodings", contract="leaktest.openapi.yaml")["encodings"]
    for encoding in encodings:
        body = check(client.post("/leak-test", json={"agent_id": agent_id, "encoding": encoding}),
                     "/leak-test", "post", contract="leaktest.openapi.yaml")
        assert body["blocked"] is True
        assert body["encoding"] == encoding or encoding == "random"
    fake = "mpk_live_" + secrets.token_hex(16)
    payloads = [(fake, True), ("AKIA" + secrets.token_hex(8).upper(), True), ("ordinary customer update", False)]
    for text, expected in payloads:
        response = client.post("/leak-test", json={"agent_id": agent_id, "free_text": text, "encoding": "plain"})
        result = check(response, "/leak-test", "post", contract="leaktest.openapi.yaml")
        assert result["blocked"] is expected
    safe_feed = fake not in json.dumps(client.get("/events").json())
    assert safe_feed
    assert client.post("/leak-test", json={"agent_id": agent_id, "encoding": "unknown"}).status_code == 400
    assert client.post("/leak-test", json={"agent_id": agent_id, "secret_ref": "missing"}).status_code == 400


def test_pagination_errors_cors_and_headers(client, scenario):
    agent_id = scenario[1]["agents"][0]["id"]
    start(client, agent_id)
    for _ in range(3):
        client.app.state.mock.tick()
    feed = check(client.get("/events", params={"limit": 2}), "/events")
    assert len(feed["events"]) == 2
    ids = [event["event_id"] for event in sorted(client.app.state.mock.events, key=lambda e: e["event_id"])]
    page = check(client.get("/events", params={"since": ids[0], "limit": 2}), "/events")
    assert [event["event_id"] for event in page["events"]] == ids[1:3]
    assert client.get("/events", params={"limit": 501}).status_code == 422
    for path in ("/badge/missing", "/burn/missing/blast-radius", "/agents/missing/ledger"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers[MOCK_HEADER] == "true"
    for path in ("/sessions", "/burn", "/leak-test"):
        assert client.post(path, json={"agent_id": "missing"}).status_code == 404
    cors = client.options("/sessions", headers={"origin": "http://localhost:5173",
                          "access-control-request-method": "POST",
                          "access-control-request-headers": "x-burn-demo-token"})
    assert cors.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert cors.headers[MOCK_HEADER] == "true"
    assert MOCK_HEADER in client.get("/events", headers={"origin": "http://localhost:5173"}).headers["access-control-expose-headers"]


def test_background_activity_starts_and_stops(scenario):
    app = create_app(scenario[0], activity_interval=0.01)
    with TestClient(app) as client:
        start(client, scenario[1]["agents"][0]["id"])
        # Run on the server loop and allow the actual background task to fire.
        client.portal.call(asyncio.sleep, 0.05)
        assert any(event["action"] == "recorded" for event in client.get("/events").json()["events"])


def test_all_http_contract_routes_are_registered(client):
    import yaml

    routes = {(route.path, method.lower()) for route in client.app.routes if hasattr(route, "methods")
              for method in route.methods}
    for contract in ("burn-api.openapi.yaml", "leaktest.openapi.yaml"):
        paths = yaml.safe_load((CONTRACTS / contract).read_text())["paths"]
        expected = {(path, method) for path, operations in paths.items() for method in operations}
        assert expected <= routes


def test_flags_one_per_credential_with_instance_count_and_owner_decisions(client, scenario):
    agents = [agent["id"] for agent in scenario[1]["agents"]]
    shared = shared_secret_ref(scenario[1]["shared_credentials"][0]["name"])
    state = client.app.state.mock
    for agent_id in agents[:2]:
        start(client, agent_id)
        state.record(agent_id, shared, tool="read_config")
        check(client.post("/burn", json={"agent_id": agent_id}), "/burn", "post")
    flags = check(client.get("/flags"), "/flags")["flags"]
    [flag] = [f for f in flags if f["secret_ref"] == shared]  # one flag, however many instances saw it
    assert (flag["flag_level"], flag["instance_count"], flag["state"]) == ("seen", 2, "open")
    assert client.post("/flags/resolve", json={"secret_ref": shared, "action": "dismiss"}).status_code == 422
    done = check(client.post("/flags/resolve", json={"secret_ref": shared, "action": "dismiss",
                                                     "reason": "test fixture", "by": "sec-team"}),
                 "/flags/resolve", "post")
    assert done["flag"]["state"] == "dismissed" and done["flag"]["resolution"]["reason"] == "test fixture"
    rotated = check(client.post("/flags/resolve", json={"secret_ref": shared, "action": "rotate",
                                                        "reason": "rotate anyway"}), "/flags/resolve", "post")
    assert rotated["flag"]["state"] == "rotated" and rotated["rotated_to_version"] == 2
    assert all(f["secret_ref"] != shared for f in check(client.get("/flags"), "/flags")["flags"])
    assert client.post("/flags/resolve", json={"secret_ref": shared, "action": "dismiss",
                                               "reason": "gone"}).status_code == 404
    actions = [e["action"] for e in check(client.get("/events"), "/events")["events"]]
    assert "dismissed" in actions


def test_roles_carry_canaries_and_auto_burn(client):
    body = {"name": "helper", "tools": ["read_ticket"], "auto_burn": False}
    role = check(client.post("/roles", json=body), "/roles", "post", 201)
    assert (role["canaries"], role["auto_burn"]) == (True, False)
