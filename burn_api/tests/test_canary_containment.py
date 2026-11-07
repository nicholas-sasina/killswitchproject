"""Canary incident -> selective containment, end to end: provider -> Burn API -> minter, on moto.

The real handlers run in-process (scripts/canary_harness.py). Session creation calling the
canary hook, the Gateway's {{canary}} ledger write and the leak are simulated by the harness.
"""
import json
import threading

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import canary
import canary_harness as harness
import db
from burn_contracts.events import make_event

WORKERS = 20
NOTIFIER = {"agent-notifier": {"id": "agent-notifier", "role": "notifier", "tools": ["notify"],
                               "credentials": ["api_key"]}}


@pytest.fixture
def stack(monkeypatch):
    for name, value in harness.ENV.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        s = harness.Stack(workers=WORKERS, extra_agents=NOTIFIER)
        s.wire(monkeypatch.setattr)
        yield s


@pytest.fixture
def fleet(stack):
    """20 instances of role worker (own agent id and session each), each with its own canary."""
    return {a: stack.start_session(a) for a in harness.worker_ids(WORKERS)}


WORKER_IDS = harness.worker_ids(WORKERS)  # agent-worker-000001 ...
VICTIM = WORKER_IDS[6]


def leak_and_present(stack, session):
    stolen = harness.attacker_decode(harness.leak_gzip_base64(stack.read_canary(session)))
    return stack.present(stolen)


def statuses(stack, fleet):
    return {a: stack.session_status(s["session_id"]) for a, s in fleet.items()}


def test_every_session_gets_its_own_canary(stack, fleet):  # 1, 2
    refs = [s["canary"]["canary_ref"] for s in fleet.values()]
    assert len(set(refs)) == WORKERS
    assert len({stack.secret(r) for r in refs}) == WORKERS


def test_reading_a_canary_records_exposure_but_no_incident(stack, fleet):  # 5
    stack.read_canary(fleet[VICTIM])
    assert stack.table("burn-exposures").get_item(
        Key={"agent_id": VICTIM, "secret_ref_or_fp": fleet[VICTIM]["canary"]["canary_ref"]}).get("Item")
    assert stack.events("canary_used") == [] and stack.incidents == []
    assert set(statuses(stack, fleet).values()) == {"active"}


def test_leaked_canary_contains_exactly_that_instance(stack, fleet):  # 6, 9, 10
    victim = fleet[VICTIM]
    keys_before = {a: stack.secret(f"burn/{a}/api_key") for a in fleet}
    status, body = leak_and_present(stack, victim)
    assert (status, body) == (401, {"error": "invalid key"})

    [used] = stack.events("canary_used")
    assert (used["agent_id"], used["session_id"]) == (VICTIM, victim["session_id"])
    [result] = stack.incidents
    assert result["outcome"] == "contained" and result["session_id"] == victim["session_id"]
    assert result["rotated"] == [f"burn/{VICTIM}/api_key", f"burn/{VICTIM}/aws_key"]

    states = statuses(stack, fleet)
    assert states.pop(VICTIM) == "revoked"
    assert set(states.values()) == {"active"} and len(states) == WORKERS - 1
    for agent in states:  # the other 19 keep working with unchanged keys
        assert stack.secret(f"burn/{agent}/api_key") == keys_before[agent]
        assert stack.present(keys_before[agent])[0] == 200
    revoked = stack.events("revoked")
    assert [e["session_id"] for e in revoked] == [victim["session_id"]]
    assert used["event_id"] in revoked[0]["detail"] and "read_config" in revoked[0]["detail"]
    print(f"\ntimings_ms (moto, in-process): {result['timings_ms']}")


def test_old_credentials_of_the_contained_instance_are_rejected(stack, fleet):  # 11
    old_key = stack.secret(f"burn/{VICTIM}/api_key")
    old_aws = json.loads(stack.secret(f"burn/{VICTIM}/aws_key"))
    leak_and_present(stack, fleet[VICTIM])
    assert stack.present(old_key) == (401, {"error": "key rotated"})
    assert stack.present(stack.secret(f"burn/{VICTIM}/api_key"))[0] == 200
    keys = boto3.client("iam").list_access_keys(UserName=old_aws["iam_user"])["AccessKeyMetadata"]
    assert {k["AccessKeyId"]: k["Status"] for k in keys}[old_aws["access_key_id"]] == "Inactive"


def test_duplicate_presentations_rotate_once(stack, fleet):  # 12
    stolen = stack.read_canary(fleet[VICTIM])
    for _ in range(5):
        assert stack.present(stolen)[0] == 401
    assert len(stack.events("canary_used")) == 1 and len(stack.incidents) == 1
    assert int(stack.credential(f"burn/{VICTIM}/api_key")["version"]) == 2
    assert int(stack.credential(fleet[VICTIM]["canary"]["canary_ref"])["presentations"]) == 5


def test_concurrent_incidents_contain_once(stack, fleet, monkeypatch):  # 13
    """Bypass the provider's dedupe and race 6 incident handlers. Each passes the attribution checks
    first (barrier); the revoke itself is serialised as DynamoDB's per-item conditional write is
    (moto is not thread-safe)."""
    stack.read_canary(fleet[VICTIM])
    ref = fleet[VICTIM]["canary"]["canary_ref"]
    barrier, lock = threading.Barrier(6), threading.Lock()
    real = canary._revoke_if_active

    def atomic(session_id, event_id):
        barrier.wait()
        with lock:
            return real(session_id, event_id)

    monkeypatch.setattr(canary, "_revoke_if_active", atomic)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        canary.handle_incident({"secret_ref": ref, "event_id": None}))) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r["outcome"] for r in results) == ["already_contained"] * 5 + ["contained"]
    assert int(stack.credential(f"burn/{VICTIM}/api_key")["version"]) == 2
    assert len(stack.events("revoked")) == 1 and len(stack.events("rotated")) == 2


def test_rotation_failure_keeps_the_instance_revoked_and_says_so(stack, fleet, monkeypatch):  # 14
    import minter_client
    real = stack.minter_call

    def failing(method, path, body=None, query=None):
        if path.endswith("/rotate"):
            raise minter_client.MinterError("minter POST rotate failed: 500")
        return real(method, path, body, query)

    monkeypatch.setattr(minter_client, "call", failing)
    old_key = stack.secret(f"burn/{VICTIM}/api_key")
    leak_and_present(stack, fleet[VICTIM])
    [result] = stack.incidents
    assert result["outcome"] == "contained_rotation_failed" and result["rotated"] == []
    assert stack.session_status(fleet[VICTIM]["session_id"]) == "revoked"
    assert stack.secret(f"burn/{VICTIM}/api_key") == old_key
    assert any("containment incomplete" in e["detail"] for e in stack.events("flagged"))


def _no_one_revoked(stack, fleet):
    assert set(statuses(stack, fleet).values()) == {"active"}
    assert stack.events("revoked") == [] and stack.events("rotated") == []


def test_canary_never_delivered_is_recorded_not_contained(stack, fleet):  # 15
    stolen = stack.secret(fleet[VICTIM]["canary"]["canary_ref"])  # no ledger row: insider/external copy
    assert stack.present(stolen)[0] == 401
    assert len(stack.events("canary_used")) == 1
    assert stack.incidents[0]["outcome"] == "not_actionable" and "no exposure" in stack.incidents[0]["reason"]
    _no_one_revoked(stack, fleet)


def test_canary_that_reached_two_agents_is_ambiguous(stack, fleet):  # 15
    ref = fleet[VICTIM]["canary"]["canary_ref"]
    stolen = stack.read_canary(fleet[VICTIM])
    stack.expose(WORKER_IDS[7], ref)  # inherited via send_message
    stack.present(stolen)
    assert stack.incidents[0]["outcome"] == "not_actionable" and WORKER_IDS[7] in stack.incidents[0]["reason"]
    _no_one_revoked(stack, fleet)


def test_session_mismatch_never_contains(stack, fleet):  # 15
    stolen = stack.read_canary(fleet[VICTIM])
    stack.table("burn-sessions").update_item(
        Key={"session_id": fleet[VICTIM]["session_id"]}, UpdateExpression="SET agent_id = :a",
        ExpressionAttributeValues={":a": WORKER_IDS[8]})
    stack.present(stolen)
    assert stack.incidents[0]["outcome"] == "not_actionable"
    assert stack.session_status(fleet[VICTIM]["session_id"]) == "active"
    assert stack.events("rotated") == []


@pytest.mark.parametrize("payload", [
    {"secret_ref": f"burn/{VICTIM}/api_key"},  # a real key's ref is not a canary
    {"secret_ref": f"burn/{VICTIM}/canary_" + "0" * 24},  # well-formed, never issued
    {"secret_ref": None}, "junk",
])
def test_forged_or_malformed_incidents_never_contain(stack, fleet, payload):  # 15
    import handler
    result = handler.handler({"canary_incident": payload})
    assert result["outcome"] in ("ignored", "not_actionable")
    _no_one_revoked(stack, fleet)


def test_incident_cannot_be_triggered_through_the_http_api(stack, fleet):
    stack.read_canary(fleet[VICTIM])
    status, _ = stack.api("POST", "/burn", None)  # an HTTP body never reaches the incident path
    assert status == 400
    import handler
    event = {"requestContext": {"http": {"method": "POST", "path": "/sessions"}}, "headers": {},
             "body": json.dumps({"canary_incident": {"secret_ref": fleet[VICTIM]["canary"]["canary_ref"]}})}
    assert handler.handler(event)["statusCode"] == 401
    _no_one_revoked(stack, fleet)


def test_sessions_sharing_an_agents_credentials_defer_rotation(stack):  # 9, 10, 15
    """Guard: if live sessions ever share one agent id (#38 gives each instance its own), revoke only
    the one whose canary leaked and never rotate credentials the others still use."""
    agent = WORKER_IDS[0]
    first = stack.start_session(agent)
    second = stack.add_sibling_session(agent)
    third = stack.add_sibling_session(agent)
    old_key = stack.secret(f"burn/{agent}/api_key")
    leak_and_present(stack, second)
    [result] = stack.incidents
    assert result["outcome"] == "contained_rotation_deferred" and result["session_id"] == second["session_id"]
    assert sorted(result["rotation_deferred_for"]) == sorted([first["session_id"], third["session_id"]])
    assert stack.session_status(second["session_id"]) == "revoked"
    assert stack.session_status(first["session_id"]) == stack.session_status(third["session_id"]) == "active"
    assert stack.secret(f"burn/{agent}/api_key") == old_key and stack.present(old_key)[0] == 200
    assert any("rotation deferred" in e["detail"] for e in stack.events("flagged"))


def test_shared_credentials_are_flagged_not_rotated(stack, fleet):  # 16, 17
    stack.minter_call("POST", "/shared/credentials",
                      {"name": "crm_db_url", "type": "other", "template": "postgres://svc:{random}@db/crm"})
    shared_value = stack.secret("burn/shared/crm_db_url")
    stack.expose(VICTIM, "burn/shared/crm_db_url")
    leak_and_present(stack, fleet[VICTIM])
    assert stack.incidents[0]["flagged"] == ["burn/shared/crm_db_url"]
    row = stack.credential("burn/shared/crm_db_url")
    assert (row["status"], row["flag_level"]) == ("flagged", "seen")
    assert row["flag_evidence_seen"]["session_id"] == fleet[VICTIM]["session_id"]
    assert stack.secret("burn/shared/crm_db_url") == shared_value
    [flag_event] = [e for e in stack.events("flagged") if e["secret_ref"] == "burn/shared/crm_db_url"]
    assert flag_event["flag_level"] == "seen" and flag_event["session_id"] == fleet[VICTIM]["session_id"]
    status, ledger = stack.api("GET", f"/agents/{VICTIM}/ledger")
    entry = next(e for e in ledger["entries"] if e["secret_ref"] == "burn/shared/crm_db_url")
    assert (entry["status"], entry["flag_level"]) == ("flagged", "seen")


def test_blocked_attempt_raises_shared_credential_to_attempted(stack, fleet):  # 18
    stack.minter_call("POST", "/shared/credentials",
                      {"name": "crm_db_url", "type": "other", "template": "postgres://svc:{random}@db/crm"})
    stack.expose(VICTIM, "burn/shared/crm_db_url")
    blocked = make_event(agent_id=VICTIM, source="egress", action="blocked", session_id=fleet[VICTIM]["session_id"],
                         secret_ref="burn/shared/crm_db_url", owner="shared", destination="paste.example",
                         encoding="base64", detail="blocked")
    stack.table("burn-events").put_item(Item={"stream": "all", **blocked})
    leak_and_present(stack, fleet[VICTIM])
    row = stack.credential("burn/shared/crm_db_url")
    assert row["flag_level"] == "attempted"
    assert row["flag_evidence_attempted"]["event_id"] == blocked["event_id"]
    assert row["flag_evidence_seen"]["source"] == "burn"


def test_blocked_events_unavailable_does_not_break_containment(stack, fleet, monkeypatch):
    real = db.query_all

    def no_index(tbl, **kwargs):
        if kwargs.get("IndexName") == "by_agent" and tbl.name == "burn-events":
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Query")
        return real(tbl, **kwargs)

    monkeypatch.setattr(db, "query_all", no_index)
    leak_and_present(stack, fleet[VICTIM])
    assert stack.incidents[0]["outcome"] == "contained"


def test_canary_from_an_exited_session_is_attributed_without_new_rotation(stack, fleet):  # 8
    stolen = stack.read_canary(fleet[VICTIM])
    assert stack.api("POST", "/burn", {"agent_id": VICTIM})[0] == 200  # session ended by a manual burn
    assert int(stack.credential(f"burn/{VICTIM}/api_key")["version"]) == 2
    stack.present(stolen)
    [used] = stack.events("canary_used")
    assert (used["agent_id"], used["session_id"]) == (VICTIM, fleet[VICTIM]["session_id"])
    assert stack.incidents[0]["outcome"] == "already_contained"
    assert int(stack.credential(f"burn/{VICTIM}/api_key")["version"]) == 2  # no extra rotation


def test_manual_burn_still_works_and_leaves_the_canary_alone(stack, fleet):  # 25
    ref = fleet[VICTIM]["canary"]["canary_ref"]
    canary_value = stack.secret(ref)
    stack.read_canary(fleet[VICTIM])
    status, body = stack.api("POST", "/burn", {"agent_id": VICTIM, "reason": "manual"})
    assert status == 200 and body["sessions_revoked"] == [fleet[VICTIM]["session_id"]]
    assert body["rotated"] == [f"burn/{VICTIM}/api_key", f"burn/{VICTIM}/aws_key"] and body["flagged"] == []
    assert stack.secret(ref) == canary_value and stack.credential(ref)["status"] == "active"
    others = statuses(stack, fleet)
    others.pop(VICTIM)
    assert set(others.values()) == {"active"}
