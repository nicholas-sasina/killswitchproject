import boto3
from conftest import call


def test_auth_required(env):
    assert call(env, "GET", "/events", token="wrong")[0] == 401
    assert call(env, "GET", "/events", token="")[0] == 401


def test_unknown_route_404(env):
    assert call(env, "GET", "/nope")[0] == 404


def test_session_unknown_agent_and_bad_id(env):
    assert call(env, "POST", "/sessions", {"agent_id": "agent-zzz"})[0] == 404
    assert call(env, "POST", "/sessions", {"agent_id": "bad id"})[0] == 400


def test_session_flow(env):
    status, body = call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    assert status == 201
    assert body["badge_ref"] == "burn/agent-a/badge"
    assert {c["type"] for c in body["credentials"]} == {"api_key", "aws_key"}
    row = env.ddb.Table("burn-sessions").get_item(Key={"session_id": body["session_id"]})["Item"]
    assert row["owner_sub"] == "person-123" and row["owner_name"] == "tester"
    assert body["owner_name"] == "tester"
    assert row["status"] == "active" and row["tools"] == ["read_ticket"] and row["send_to"] == ["agent-b"]
    assert boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="burn/agent-a/badge")["SecretString"]
    _, feed = call(env, "GET", "/events")
    assert feed["events"][-1]["action"] == "issued"
    assert "stream" not in feed["events"][-1]


def test_new_session_revokes_old(env):
    first = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]["session_id"]
    second = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]["session_id"]
    assert call(env, "GET", f"/badge/{first}")[1]["status"] == "revoked"
    assert call(env, "GET", f"/badge/{second}")[1]["status"] == "active"
    assert len(env.badges.deleted) == 1


def test_minter_failure_creates_no_session(env):
    env.minter.fail = True
    assert call(env, "POST", "/sessions", {"agent_id": "agent-a"})[0] == 502
    assert env.ddb.Table("burn-sessions").scan()["Items"] == []


def _expose(env, agent_id, ref, owner, status="active"):
    env.ddb.Table("burn-exposures").put_item(Item={
        "agent_id": agent_id, "secret_ref_or_fp": ref, "secret_ref": ref, "fingerprint": "a" * 64,
        "secret_type": "api_key", "owner": owner, "source": "tool_response", "first_seen": "2026-01-01T00:00:00Z"})
    env.ddb.Table("burn-credentials").put_item(Item={"secret_ref": ref, "status": status})


def test_burn_rotates_own_and_flags_shared(env):
    sid = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]["session_id"]
    _expose(env, "agent-a", "burn/agent-a/api_key", "agent")
    _expose(env, "agent-a", "burn/shared/crm_db_url", "shared")
    status, body = call(env, "POST", "/burn", {"agent_id": "agent-a", "reason": "test"})
    assert status == 200
    assert body["sessions_revoked"] == [sid] and body["revoked"] is True
    assert body["rotated"] == ["burn/agent-a/api_key"] and body["flagged"] == ["burn/shared/crm_db_url"]
    assert call(env, "GET", f"/badge/{sid}")[1]["status"] == "revoked"
    actions = [e["action"] for e in call(env, "GET", "/events")[1]["events"]]
    assert actions == ["issued", "revoked", "rotated", "flagged"]


def test_burn_with_no_session_and_empty_ledger(env):
    status, body = call(env, "POST", "/burn", {"agent_id": "agent-b"})
    assert status == 200 and body["sessions_revoked"] == [] and body["rotated"] == []
    assert env.minter.calls[-1][2] == {"exposed_refs": []}


def test_burn_does_not_touch_other_agents(env):
    call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    other = call(env, "POST", "/sessions", {"agent_id": "agent-b"})[1]["session_id"]
    call(env, "POST", "/burn", {"agent_id": "agent-a"})
    assert call(env, "GET", f"/badge/{other}")[1]["status"] == "active"


def test_burn_unknown_agent(env):
    assert call(env, "POST", "/burn", {"agent_id": "agent-zzz"})[0] == 404


def test_events_since_limit_and_agents(env):
    call(env, "POST", "/sessions", {"agent_id": "agent-a"})
    call(env, "POST", "/burn", {"agent_id": "agent-a"})
    _, feed = call(env, "GET", "/events")
    ids = [e["event_id"] for e in feed["events"]]
    assert ids == sorted(ids) and feed["cursor"] == ids[-1]
    _, newer = call(env, "GET", "/events", query={"since": ids[0]})
    assert [e["event_id"] for e in newer["events"]] == ids[1:]
    _, last = call(env, "GET", "/events", query={"limit": "1"})
    assert [e["event_id"] for e in last["events"]] == ids[-1:]
    states = {a["agent_id"]: a for a in feed["agents"]}
    assert states["agent-a"]["status"] == "revoked" and states["agent-b"]["status"] == "idle"
    assert states["agent-a"]["role"] == "reader"


def test_ledger_joins_credential_status(env):
    _expose(env, "agent-a", "burn/agent-a/api_key", "agent", status="rotated")
    status, body = call(env, "GET", "/agents/agent-a/ledger")
    assert status == 200 and body["entries"][0]["status"] == "rotated"
    assert call(env, "GET", "/agents/agent-b/ledger")[1]["entries"] == []


def test_badge_unknown(env):
    assert call(env, "GET", "/badge/sess_nope")[0] == 404


def test_blast_radius(env):
    status, body = call(env, "GET", "/burn/agent-a/blast-radius")
    assert status == 200 and len(body["events"]) == 2 and "lag" in body["note"]
    assert {k["key_ref"]: k["status"] for k in body["keys"]} == {
        "burn/agent-a/aws_key#v1": "inactive", "burn/agent-a/aws_key#v2": "active"}


def test_preflight_needs_no_token_but_other_methods_do(env):
    import handler
    for path in ("/events", "/burn"):
        event = {"requestContext": {"http": {"method": "OPTIONS", "path": path}},
                 "headers": {"origin": "http://localhost:5173", "access-control-request-method": "POST"}}
        out = handler.handler(event)
        assert out["statusCode"] == 204
        assert out["headers"]["access-control-allow-origin"] == "http://localhost:5173"
        assert "authorization" in out["headers"]["access-control-allow-headers"]
    assert call(env, "GET", "/events", token="")[0] == 401
    assert call(env, "POST", "/burn", {"agent_id": "agent-a"}, token="")[0] == 401


def test_signed_in_owner_is_server_derived(env):
    import handler
    event = {"requestContext": {"http": {"method": "POST", "path": "/sessions"},
                                 "authorizer": {"jwt": {"claims": {"sub": "unique-sub", "username": "alice"}}}},
             "headers": {"authorization": "Bearer arbitrary-text"},
             "body": '{"agent_id":"agent-b","owner_sub":"forged","owner_name":"forged"}'}
    import json
    reply = handler.handler(event)
    assert reply["statusCode"] == 201
    data = json.loads(reply["body"])
    assert data["owner_sub"] == "unique-sub" and data["owner_name"] == "alice"
    stored = env.ddb.Table("burn-sessions").get_item(Key={"session_id": data["session_id"]})["Item"]
    assert stored["owner_sub"] == "unique-sub"
    assert call(env, "GET", "/events")[1]["agents"][1]["owner_name"] == "alice"

def test_spoofed_identity_header_is_not_accepted(env):
    import handler
    response = handler.handler({"requestContext": {"http": {"method": "GET", "path": "/events"}},
                                "headers": {"x-user-sub": "admin", "authorization": "Bearer fake"}})
    assert response["statusCode"] == 401
