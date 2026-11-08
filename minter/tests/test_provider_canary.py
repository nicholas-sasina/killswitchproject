"""Mock provider and canaries: always 401, one attributed event, bounded and idempotent."""
import base64
import gzip
import json
import threading

import boto3
import pytest

import canary_trip
import dispatch
import minter_app
import provider_app
import session_proof
from burn_contracts.names import instance_agent_id
from conftest import fingerprint_rows

AGENT, SESSION = instance_agent_id("worker", "3fa91c"), "sess_0123456789abcdef"


def minter(method, path, body=None):
    return minter_app.handler({"method": method, "path": path, "body": body or {}})["body"]


def whoami(token, ip="198.51.100.7"):
    resp = provider_app.handler({"rawPath": "/v1/whoami", "headers": {"authorization": f"Bearer {token}"},
                                 "requestContext": {"http": {"sourceIp": ip}}})
    return resp["statusCode"], json.loads(resp["body"])


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatch, "invoke", lambda fn, payload, wait: calls.append((fn, payload, wait)))
    return calls


@pytest.fixture
def canary():
    meta = minter("POST", f"/sessions/{SESSION}/canary", {"agent_id": AGENT})
    value = boto3.client("secretsmanager").get_secret_value(SecretId=meta["canary_ref"])["SecretString"]
    return meta, value


def test_canary_never_authenticates_and_looks_like_an_unknown_key(canary, dispatched):  # 4
    _, value = canary
    unknown = whoami("mpk_live_" + "Z" * 32)
    assert whoami(value) == unknown == (401, {"error": "invalid key"})


def test_canary_rejected_even_by_a_provider_that_ignores_the_canary_marker(canary, tables, dispatched):  # 4
    meta, value = canary
    rows = [r for r in fingerprint_rows(tables) if r["secret_ref"] == meta["canary_ref"]]
    for row in rows:  # an older provider build never looked at `canary`
        tables["fingerprints"].put_item(Item={k: v for k, v in row.items() if k != "canary"})
    assert whoami(value) == (401, {"error": "invalid key"})  # secret_type is not api_key


def test_presenting_creates_one_attributed_canary_used_event(canary, tables, dispatched):  # 6
    meta, value = canary
    assert whoami(value)[0] == 401
    events = tables["events"].scan()["Items"]
    assert len(events) == 1
    e = events[0]
    assert (e["source"], e["action"], e["agent_id"], e["session_id"]) == ("provider", "canary_used", AGENT, SESSION)
    assert "instance_id" not in e
    assert e["secret_ref"] == meta["canary_ref"] and e["owner_agent_id"] == AGENT and len(e["fingerprint"]) == 64
    assert "198.51.100.7" in e["detail"]
    assert value not in json.dumps(events, default=str)
    assert dispatched == [("burn-api", {"canary_incident": {"secret_ref": meta["canary_ref"],
                                                              "event_id": e["event_id"]}}, False)]
    row = tables["credentials"].get_item(Key={"secret_ref": meta["canary_ref"]})["Item"]
    assert (row["status"], row["presentations"], row["incident_event_id"]) == ("tripped", 1, e["event_id"])


def test_leak_encoding_does_not_matter(canary, tables, dispatched):
    """gzip+base64 got past the exit check; the attacker still has to present the plain value here."""
    meta, value = canary
    blob = base64.b64encode(gzip.compress(value.encode())).decode()
    assert value not in blob
    stolen = gzip.decompress(base64.b64decode(blob)).decode()
    assert whoami(stolen)[0] == 401
    assert tables["events"].scan()["Items"][0]["session_id"] == SESSION


def test_repeated_presentations_are_counted_not_repeated(canary, tables, dispatched):  # 12 (provider side)
    meta, value = canary
    for _ in range(10):
        assert whoami(value)[0] == 401
    assert len(tables["events"].scan()["Items"]) == 1 and len(dispatched) == 1
    assert tables["credentials"].get_item(Key={"secret_ref": meta["canary_ref"]})["Item"]["presentations"] == 10


def test_concurrent_presentations_dispatch_once(canary, tables, dispatched, monkeypatch):  # 13 (provider side)
    """DynamoDB applies a conditional update atomically per item. moto is not thread-safe, so the
    write itself is serialised here, after every request has already looked the canary up."""
    meta, value = canary
    barrier, lock = threading.Barrier(8), threading.Lock()
    real = canary_trip._count_presentation

    def atomic(*args):
        barrier.wait()
        with lock:
            return real(*args)

    monkeypatch.setattr(canary_trip, "_count_presentation", atomic)
    threads = [threading.Thread(target=whoami, args=(value,)) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(tables["events"].scan()["Items"]) == 1 and len(dispatched) == 1


def test_failed_dispatch_is_retried_on_the_next_presentation(canary, tables, monkeypatch):
    meta, value = canary
    calls = []

    def flaky(fn, payload, wait):
        calls.append(payload)
        if len(calls) == 1:
            raise RuntimeError("throttled")

    monkeypatch.setattr(dispatch, "invoke", flaky)
    assert whoami(value)[0] == 401 and whoami(value)[0] == 401 and whoami(value)[0] == 401
    assert len(calls) == 2 and calls[0] == calls[1]  # same incident, same event id
    assert len(tables["events"].scan()["Items"]) == 1


def test_recording_failure_still_answers_401(canary, monkeypatch):
    _, value = canary
    monkeypatch.setattr(canary_trip, "on_presented", lambda *a: (_ for _ in ()).throw(RuntimeError("ddb down")))
    assert whoami(value) == (401, {"error": "invalid key"})


def test_canary_without_registry_row_is_recorded_but_never_dispatched(canary, tables, dispatched):  # 15
    meta, value = canary
    tables["credentials"].delete_item(Key={"secret_ref": meta["canary_ref"]})
    assert whoami(value)[0] == 401
    events = tables["events"].scan()["Items"]
    assert len(events) == 1 and "not contained" in events[0]["detail"] and dispatched == []


def test_unknown_credential_triggers_nothing(canary, tables, dispatched):  # 7
    _, value = canary
    for token in ("mpk_live_" + "q" * 32, value.lower(), value[:-1], value[9:]):
        assert whoami(token) == (401, {"error": "invalid key"})
    assert tables["events"].scan()["Items"] == [] and dispatched == []


def test_canary_stays_attributable_after_its_agent_is_burned_and_reset(canary, tables, dispatched):  # 8
    meta, value = canary
    minter("POST", f"/agents/{AGENT}/credentials", {"types": ["api_key"]})
    minter("POST", f"/agents/{AGENT}/rotate", {})
    minter("DELETE", f"/agents/{AGENT}/credentials")
    assert whoami(value)[0] == 401
    e = tables["events"].scan()["Items"][0]
    assert (e["action"], e["session_id"], e["agent_id"]) == ("canary_used", SESSION, AGENT)


# ---- shared credentials presented at the provider -> `reported` ---------------------------

@pytest.fixture
def shared_key(monkeypatch):
    def invoke(fn, payload, wait):
        assert fn == "burn-minter" and wait
        return minter_app.handler(payload)

    monkeypatch.setattr(dispatch, "invoke", invoke)
    minter("POST", "/shared/credentials", {"name": "partner_key", "type": "api_key", "template": "mpk_live_{random}"})
    return boto3.client("secretsmanager").get_secret_value(SecretId="burn/shared/partner_key")["SecretString"]


def test_flagged_shared_key_used_at_provider_becomes_reported(shared_key, tables):  # 19
    assert whoami(shared_key)[0] == 200  # unflagged: legitimate use, nothing recorded
    assert tables["events"].scan()["Items"] == []
    minter("POST", "/flags", {"secret_refs": ["burn/shared/partner_key"], "level": "seen",
                              "evidence": {"source": "burn", "agent_id": "agent-a"}})
    assert whoami(shared_key)[0] == 200  # still works: shared keys are never auto-rotated
    row = tables["credentials"].get_item(Key={"secret_ref": "burn/shared/partner_key"})["Item"]
    assert row["flag_level"] == "reported" and row["flag_evidence_reported"]["source"] == "provider"
    assert row["flag_evidence_seen"]["agent_id"] == "agent-a"
    whoami(shared_key)
    events = tables["events"].scan()["Items"]
    assert [(e["action"], e["flag_level"]) for e in events] == [("flagged", "reported")]  # once
    assert shared_key not in json.dumps(events, default=str)


# ---- origin: only use from outside Burn may burn ------------------------------------------

def key():
    from burn_contracts import fingerprint as fp
    return fp.key_from_hex(boto3.client("secretsmanager").get_secret_value(
        SecretId="burn-system/fingerprint-key")["SecretString"])


def whoami_via(token, session_id, proof=None):
    headers = {"authorization": f"Bearer {token}",
               "x-burn-session-proof": proof or session_proof.make(session_id, key())}
    resp = provider_app.handler({"rawPath": "/v1/whoami", "headers": headers})
    return resp["statusCode"], json.loads(resp["body"])


def test_use_through_its_own_session_is_an_alert_not_a_burn(canary, tables, dispatched):
    meta, value = canary
    assert whoami_via(value, SESSION) == (401, {"error": "invalid key"})
    [e] = tables["events"].scan()["Items"]
    assert e["action"] == "canary_used" and "own session; alert only, not burned" in e["detail"]
    assert dispatched == []
    row = tables["credentials"].get_item(Key={"secret_ref": meta["canary_ref"]})["Item"]
    assert row["presentations_own_session"] == 1 and "incident_event_id" not in row


def test_use_through_another_burn_session_is_an_alert_for_review(canary, tables, dispatched):
    _, value = canary
    whoami_via(value, "sess_someoneelse00")
    [e] = tables["events"].scan()["Items"]
    assert "another Burn session sess_someoneelse00" in e["detail"] and dispatched == []


def test_an_earlier_alert_does_not_swallow_a_later_outside_use(canary, tables, dispatched):
    _, value = canary
    for _ in range(3):
        whoami_via(value, SESSION)
    whoami(value)  # now from outside
    whoami(value)
    details = sorted(e["detail"] for e in tables["events"].scan()["Items"])
    assert len(details) == 2 and any("outside Burn" in d for d in details)
    assert len(dispatched) == 1


@pytest.mark.parametrize("proof", [
    "v1.sess_0123456789abcdef.1.deadbeef",               # wrong mac
    "v2.sess_0123456789abcdef.1700000000.00",             # unknown version
    "garbage",
])
def test_forged_or_malformed_proofs_count_as_outside(canary, tables, dispatched, proof):
    _, value = canary
    whoami_via(value, SESSION, proof=proof)
    assert len(dispatched) == 1 and "outside Burn" in tables["events"].scan()["Items"][0]["detail"]


def test_stale_proofs_count_as_outside(canary, dispatched):
    import time
    _, value = canary
    whoami_via(value, SESSION, proof=session_proof.make(SESSION, key(), now=time.time() - 600))
    assert len(dispatched) == 1


def test_session_proof_round_trip_and_tamper():
    k = b"k" * 32
    proof = session_proof.make("sess.with.dots", k, now=1000)
    assert session_proof.verified_session({"X-Burn-Session-Proof": proof}, k, now=1050) == "sess.with.dots"
    assert session_proof.verified_session({"x-burn-session-proof": proof}, k, now=1200) is None
    assert session_proof.verified_session({"x-burn-session-proof": proof}, b"x" * 32, now=1050) is None
    tampered = proof.replace("sess.with.dots", "sess.other.one")
    assert session_proof.verified_session({"x-burn-session-proof": tampered}, k, now=1050) is None
