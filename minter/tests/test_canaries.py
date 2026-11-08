"""Per-session canaries: issuance, idempotency, attribution, and what they must never do."""
import json
import re
import threading

import boto3
import pytest

import canaries
import minter_app
import randomness
from burn_contracts import fingerprint as fp
from burn_contracts.names import canary_secret_ref, instance_agent_id
from conftest import fingerprint_rows

AGENT = instance_agent_id("worker", "3fa91c")  # one instance of role worker


def call(method, path, body=None, query=None):
    resp = minter_app.handler({"method": method, "path": path, "body": body or {}, "query": query or {}})
    return resp["statusCode"], resp["body"]


def issue(session_id, agent=AGENT):
    status, out = call("POST", f"/sessions/{session_id}/canary", {"agent_id": agent})
    assert status == 200, out
    return out


def value_of(ref):
    return boto3.client("secretsmanager").get_secret_value(SecretId=ref)["SecretString"]


def hmac_key():
    return fp.key_from_hex(value_of("burn-system/fingerprint-key"))


def test_session_gets_one_canary_stored_only_as_secret_and_fingerprints(tables):  # 1
    meta = issue("sess_aaaa1111")
    ref = canary_secret_ref(AGENT, "sess_aaaa1111")
    assert meta["canary_ref"] == ref and meta["session_id"] == "sess_aaaa1111"
    assert (meta["agent_id"], meta["version"], meta["status"]) == (AGENT, 1, "active")
    assert "instance_id" not in meta  # the session id is the instance's identity
    assert meta["issued_at"].endswith("Z")
    value = value_of(ref)
    assert re.fullmatch(r"mpk_live_[A-Za-z0-9]{32}", value)  # indistinguishable from a real api_key
    assert value not in json.dumps(meta)  # the API never returns the value

    row = tables["credentials"].get_item(Key={"secret_ref": ref})["Item"]
    assert (row["secret_type"], row["owner_agent_id"], row["session_id"]) == ("canary", AGENT, "sess_aaaa1111")
    assert value not in json.dumps(row, default=str)

    exact = tables["fingerprints"].get_item(Key={"fingerprint": fp.fingerprint(value, hmac_key())})["Item"]
    assert exact["canary"] is True and exact["secret_type"] == "other" and exact["kind"] == "exact"
    assert (exact["session_id"], exact["owner_agent_id"]) == ("sess_aaaa1111", AGENT)
    rows = [r for r in fingerprint_rows(tables) if r["secret_ref"] == ref]
    assert {r["kind"] for r in rows} == {"exact", "shingle"} and all(r["canary"] for r in rows)
    assert not any(value in json.dumps(r, default=str) for r in rows)


def test_two_sessions_of_the_same_role_get_different_canaries():  # 2
    a, b = issue("sess_one"), issue("sess_two")
    c = issue("sess_three", agent=instance_agent_id("worker", "0b7c2e"))  # another worker instance
    refs = {a["canary_ref"], b["canary_ref"], c["canary_ref"]}
    assert len(refs) == 3 and len({value_of(r) for r in refs}) == 3


def test_retrying_issuance_is_idempotent(tables):  # 3
    first = issue("sess_retry")
    value = value_of(first["canary_ref"])
    rows_before = tables["fingerprints"].scan()["Count"]
    for _ in range(3):
        assert issue("sess_retry") == first
    assert value_of(first["canary_ref"]) == value
    assert tables["fingerprints"].scan()["Count"] == rows_before
    assert call("GET", "/sessions/sess_retry/canary", query={"agent_id": AGENT}) == (200, first)


def test_concurrent_issuance_for_one_session_yields_one_canary(tables):  # 3 (concurrency)
    results, errors = [], []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        try:
            results.append(issue("sess_race"))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    ref = canary_secret_ref(AGENT, "sess_race")
    value = value_of(ref)
    exact = [r for r in fingerprint_rows(tables) if r["secret_ref"] == ref and r["kind"] == "exact"]
    assert [r["fingerprint"] for r in exact] == [fp.fingerprint(value, hmac_key())]  # exactly one canary value
    assert all(r["canary_ref"] == ref for r in results)


def test_interrupted_issue_resumes_with_the_stored_value(tables, monkeypatch):
    real = canaries._register
    monkeypatch.setattr(canaries, "_register", lambda row, value: (_ for _ in ()).throw(RuntimeError("crash")))
    assert call("POST", "/sessions/sess_crash/canary", {"agent_id": AGENT})[0] == 500
    ref = canary_secret_ref(AGENT, "sess_crash")
    stored = value_of(ref)
    assert tables["credentials"].get_item(Key={"secret_ref": ref})["Item"]["status"] == "issuing"
    monkeypatch.setattr(canaries, "_register", real)
    assert issue("sess_crash")["status"] == "active"
    assert value_of(ref) == stored  # the retry finished the same canary, it did not mint a second one


def test_collision_with_a_registered_fingerprint_draws_again(monkeypatch):
    taken = issue("sess_first")
    taken_body = value_of(taken["canary_ref"])[len("mpk_live_"):]
    draws = iter([taken_body, "B" * 32])
    monkeypatch.setattr(canaries, "random_text", lambda n: next(draws))
    fresh = issue("sess_second")
    assert value_of(fresh["canary_ref"]) == "mpk_live_" + "B" * 32


def test_canary_uses_the_os_csprng(monkeypatch):
    seen = []
    real = randomness.secrets.choice
    monkeypatch.setattr(randomness.secrets, "choice", lambda seq: seen.append(1) or real(seq))
    issue("sess_rng")
    assert len(seen) >= 32


def test_canary_survives_burn_rotation_and_credential_reset(tables):  # 8 (minter side)
    call("POST", f"/agents/{AGENT}/credentials", {"types": ["api_key"]})
    meta = issue("sess_keep")
    value = value_of(meta["canary_ref"])
    status, rotated = call("POST", f"/agents/{AGENT}/rotate", {"exposed_refs": [meta["canary_ref"]]})
    assert rotated["rotated"] == [f"burn/{AGENT}/api_key"]  # canary never rotated, never flagged
    assert rotated["flagged"] == []
    assert call("DELETE", f"/agents/{AGENT}/credentials")[1] == {"deleted": [f"burn/{AGENT}/api_key"]}
    assert value_of(meta["canary_ref"]) == value
    row = tables["fingerprints"].get_item(Key={"fingerprint": fp.fingerprint(value, hmac_key())})["Item"]
    assert row["session_id"] == "sess_keep" and row["status"] == "active"


def test_canary_seen_by_another_agent_is_not_flagged():
    meta = issue("sess_x", agent="agent-a")
    _, result = call("POST", "/agents/agent-b/rotate", {"exposed_refs": [meta["canary_ref"]]})
    assert result["flagged"] == []


def test_same_ref_is_never_handed_to_a_different_holder(tables):
    meta = issue("sess_owned")
    tables["credentials"].update_item(  # a (hypothetical) hash collision with another session's row
        Key={"secret_ref": meta["canary_ref"]}, UpdateExpression="SET session_id = :s",
        ExpressionAttributeValues={":s": "sess_other"})
    status, body = call("POST", "/sessions/sess_owned/canary", {"agent_id": AGENT})
    assert status == 400 and "different session" in body["error"]


def test_delete_for_agent_is_reset_only_and_complete(tables):
    a, b = issue("sess_d1"), issue("sess_d2")
    other = issue("sess_d3", agent="agent-other")
    status, body = call("DELETE", f"/agents/{AGENT}/canaries")
    assert status == 200 and sorted(body["deleted"]) == sorted([a["canary_ref"], b["canary_ref"]])
    left = {r["secret_ref"] for r in fingerprint_rows(tables)}
    assert left == {other["canary_ref"]}
    assert call("GET", "/sessions/sess_d1/canary", query={"agent_id": AGENT})[0] == 404
    assert call("DELETE", f"/agents/{AGENT}/canaries")[1] == {"deleted": []}


@pytest.mark.parametrize("path,body", [
    ("/sessions/sess_ok/canary", {"agent_id": "Bad_Agent"}),
    ("/sessions/sess_ok/canary", {}),
    ("/sessions/has space/canary", {"agent_id": AGENT}),
    ("/sessions/sess_ok/canary", {"agent_id": 7}),
])
def test_bad_canary_requests_are_400(path, body):
    assert call("POST", path, body)[0] == 400


def test_unknown_session_canary_is_404():
    assert call("GET", "/sessions/sess_none/canary", query={"agent_id": AGENT})[0] == 404
