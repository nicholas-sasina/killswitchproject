"""Shared-credential flag levels: seen < attempted < reported, monotonic, evidence-backed, bounded."""
import json
import threading

import pytest

import flags
import minter_app
from burn_contracts.ids import new_event_id

REF = "burn/shared/crm_db_url"


def call(method, path, body=None):
    resp = minter_app.handler({"method": method, "path": path, "body": body or {}})
    return resp["statusCode"], resp["body"]


@pytest.fixture
def shared_ref():
    status, _ = call("POST", "/shared/credentials", {
        "name": "crm_db_url", "type": "other", "template": "postgres://svc:{random}@db.example/crm"})
    assert status == 200
    return REF


def flag(level, source, refs=(REF,), **evidence):
    return call("POST", "/flags", {"secret_refs": list(refs), "level": level,
                                   "evidence": {"source": source, **evidence}})


def row(tables):
    return tables["credentials"].get_item(Key={"secret_ref": REF})["Item"]


def test_initial_exposure_is_seen(shared_ref, tables):  # 17
    evt = new_event_id()
    status, body = flag("seen", "tool_response", agent_id="agent-a", session_id="sess_1", tool="read_config",
                        event_id=evt, ts="2026-11-07T20:00:00.000Z")
    assert (status, body) == (200, {"flags": [{"secret_ref": REF, "flag_level": "seen", "changed": True,
                                               "instance_count": 1}]})
    r = row(tables)
    assert (r["status"], r["flag_level"], r["flagged_by"]) == ("flagged", "seen", "agent-a")
    ev = r["flag_evidence_seen"]
    assert (ev["source"], ev["event_id"], ev["session_id"], ev["tool"]) == ("tool_response", evt, "sess_1", "read_config")


def test_blocked_outbound_attempt_is_attempted(shared_ref, tables):  # 18
    flag("seen", "tool_response", agent_id="agent-a")
    status, body = flag("attempted", "egress", agent_id="agent-a", event_id=new_event_id())
    assert body["flags"][0] == {"secret_ref": REF, "flag_level": "attempted", "changed": True, "instance_count": 1}
    assert set(row(tables)) >= {"flag_evidence_seen", "flag_evidence_attempted"}  # lower evidence kept


def test_reported_needs_a_trusted_provider_and_an_existing_flag(shared_ref, tables):  # 19
    assert flag("reported", "provider")[1] == {"flags": []}  # legit use of an unflagged key proves nothing
    assert "flag_level" not in row(tables)
    flag("seen", "burn", agent_id="agent-a")
    assert flag("reported", "provider")[1]["flags"][0]["flag_level"] == "reported"
    assert flag("reported", "egress")[0] == 400  # the proxy cannot claim a provider sighting


def test_level_never_decreases(shared_ref, tables):  # 20
    flag("seen", "burn", agent_id="agent-a")
    flag("attempted", "gateway", agent_id="agent-a", tool="notify")
    flag("reported", "provider")
    for level, source in (("seen", "tool_response"), ("attempted", "egress")):
        status, body = flag(level, source, agent_id="agent-b")
        assert body["flags"][0] == {"secret_ref": REF, "flag_level": "reported", "changed": False,
                                    "instance_count": 2}
    assert row(tables)["flag_level"] == "reported" and row(tables)["flag_rank"] == 3


def test_lower_evidence_arriving_late_is_recorded_without_lowering(shared_ref, tables):
    flag("seen", "burn", agent_id="agent-a")
    flag("reported", "provider")
    flag("attempted", "egress", agent_id="agent-c")
    r = row(tables)
    assert r["flag_level"] == "reported" and r["flag_evidence_attempted"]["agent_id"] == "agent-c"


def test_evidence_is_bounded_first_wins_and_secret_free(shared_ref, tables, secrets_client):  # 21
    first = new_event_id()
    flag("seen", "tool_response", agent_id="agent-a", event_id=first)
    for _ in range(25):
        flag("seen", "tool_response", agent_id="agent-b", event_id=new_event_id())
        flag("attempted", "egress", agent_id="agent-b", event_id=new_event_id())
    r = row(tables)
    assert r["flag_evidence_seen"]["event_id"] == first
    assert sorted(k for k in r if k.startswith("flag_evidence_")) == ["flag_evidence_attempted", "flag_evidence_seen"]
    value = secrets_client.get_secret_value(SecretId=REF)["SecretString"]
    assert value not in json.dumps(r, default=str)


@pytest.mark.parametrize("evidence", [
    {"source": "egress", "value": "mpk_live_x"},           # unknown field: a raw value has no slot
    {"source": "egress", "detail": "free text"},            # no free text either
    {"source": "egress", "agent_id": "not an agent"},
    {"source": "egress", "tool": "x" * 65},
    {"source": "egress", "fingerprint": "abc"},
    {"agent_id": "agent-a"},                                # source is required
])
def test_bad_evidence_is_rejected(shared_ref, evidence):
    status, _ = call("POST", "/flags", {"secret_refs": [REF], "level": "attempted", "evidence": evidence})
    assert status == 400


def test_bad_levels_and_ref_lists_are_rejected(shared_ref):
    assert flag("critical", "egress")[0] == 400
    assert flag("seen", "egress")[0] == 400  # a blocking check reports attempts, not sightings
    assert call("POST", "/flags", {"secret_refs": "burn/shared/x", "level": "seen",
                                   "evidence": {"source": "burn"}})[0] == 400
    assert flag("seen", "burn", refs=[REF] * 101)[0] == 400


def test_duplicate_events_are_idempotent(shared_ref, tables):  # 22
    evt = new_event_id()
    assert flag("attempted", "egress", event_id=evt)[1]["flags"][0]["changed"] is True
    before = row(tables)
    assert flag("attempted", "egress", event_id=evt)[1]["flags"][0]["changed"] is False
    after = row(tables)
    assert {k: v for k, v in after.items() if k != "flag_updated_at"} == \
           {k: v for k, v in before.items() if k != "flag_updated_at"}


def test_concurrent_updates_settle_on_the_highest_level(shared_ref, tables):  # 22 (concurrency)
    flag("seen", "burn", agent_id="agent-a")
    barrier = threading.Barrier(9)
    jobs = [("seen", "tool_response"), ("attempted", "egress"), ("reported", "provider")] * 3

    def run(level, source):
        barrier.wait()
        flag(level, source)

    threads = [threading.Thread(target=run, args=job) for job in jobs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    r = row(tables)
    assert r["flag_level"] == "reported" and r["flag_rank"] == 3
    assert {"flag_evidence_seen", "flag_evidence_attempted", "flag_evidence_reported"} <= set(r)


def test_unregistered_and_canary_refs_are_skipped(shared_ref):
    status, body = flag("seen", "burn", refs=[REF, "burn/shared/nope", "burn/agent-a/canary_" + "0" * 24])
    assert status == 200 and [f["secret_ref"] for f in body["flags"]] == [REF]


def test_flagging_never_changes_the_value(shared_ref, secrets_client):  # 16 (minter side)
    before = secrets_client.get_secret_value(SecretId=REF)["SecretString"]
    flag("seen", "burn", agent_id="agent-a")
    flag("attempted", "egress")
    flag("reported", "provider")
    assert secrets_client.get_secret_value(SecretId=REF)["SecretString"] == before
    assert len(secrets_client.list_secret_version_ids(SecretId=REF)["Versions"]) == 1


def test_legacy_rows_without_secret_type_can_still_be_flagged(tables):
    tables["credentials"].put_item(Item={"secret_ref": "burn/shared/legacy", "status": "active"})
    assert flags.raise_level("burn/shared/legacy", "seen", {"source": "burn"})["changed"] is True


# ---- one flag per credential: instance count, owner decisions ------------------------------

def resolve(action, reason="checked with the db team", **extra):
    return call("POST", "/flags/resolve", {"secret_ref": REF, "action": action, "reason": reason, **extra})


def test_one_flag_counts_distinct_instances(shared_ref, tables):
    for agent in ("agent-worker-000001", "agent-worker-000002", "agent-worker-000001"):
        flag("seen", "tool_response", agent_id=agent)
    flag("attempted", "egress", agent_id="agent-worker-000003")
    flag("reported", "provider")  # the provider is not an instance
    status, body = call("GET", "/flags")
    [only] = [f for f in body["flags"] if f["secret_ref"] == REF]
    assert (only["flag_level"], only["instance_count"], only["state"]) == ("reported", 3, "open")
    assert set(only["evidence"]) == {"seen", "attempted", "reported"} and only["resolution"] is None
    assert len([r for r in tables["credentials"].scan()["Items"] if r["secret_ref"] == REF]) == 1


def test_instance_ids_are_capped(shared_ref, tables, monkeypatch):
    monkeypatch.setattr(flags, "MAX_INSTANCES", 3)
    for i in range(6):
        flag("seen", "tool_response", agent_id=f"agent-w-{i}")
    assert flag("seen", "tool_response", agent_id="agent-w-0")[1]["flags"][0]["instance_count"] == 3


def test_dismiss_with_a_reason_closes_the_flag_and_higher_evidence_reopens_it(shared_ref, tables):
    flag("seen", "burn", agent_id="agent-a")
    flag("attempted", "egress", agent_id="agent-a")
    status, body = resolve("dismiss", by="sec-team")
    assert status == 200 and body["rotated_to_version"] is None
    assert body["flag"]["state"] == "dismissed" and body["flag"]["flag_level"] == "attempted"
    assert body["flag"]["resolution"] | {"at": None} == {
        "action": "dismiss", "reason": "checked with the db team", "by": "sec-team", "at": None, "level": "attempted"}
    assert row(tables)["status"] == "active"
    # same-or-lower evidence after dismissal: counted, but the flag stays closed
    out = flag("attempted", "egress", agent_id="agent-b")[1]["flags"][0]
    assert (out["changed"], out["instance_count"]) == (False, 2) and row(tables)["flag_state"] == "dismissed"
    # higher evidence reopens it; the level never went down
    assert flag("reported", "provider")[1]["flags"][0]["changed"] is True
    assert (row(tables)["flag_state"], row(tables)["status"], row(tables)["flag_level"]) == ("open", "flagged", "reported")


def test_rotate_mints_a_new_version_and_clears_the_flag(shared_ref, tables, secrets_client):
    from burn_contracts import fingerprint as fp
    old = secrets_client.get_secret_value(SecretId=REF)["SecretString"]
    flag("seen", "burn", agent_id="agent-a")
    status, body = resolve("rotate")
    assert status == 200 and body["rotated_to_version"] == 2 and body["flag"]["state"] == "rotated"
    new = secrets_client.get_secret_value(SecretId=REF)["SecretString"]
    assert new != old and new.startswith("postgres://svc:") and new.endswith("@db.example/crm")
    r = row(tables)
    assert r["status"] == "active" and "flag_level" not in r and r["last_flag"]["flag_level"] == "seen"
    key = fp.key_from_hex(secrets_client.get_secret_value(SecretId="burn-system/fingerprint-key")["SecretString"])
    old_fp = tables["fingerprints"].get_item(Key={"fingerprint": fp.fingerprint(old, key)})["Item"]
    assert old_fp["status"] == "rotated"  # a leak of the old value is still caught and attributed
    assert all(f["secret_ref"] != REF for f in call("GET", "/flags")[1]["flags"])
    assert resolve("dismiss")[0] == 404  # nothing left to decide


def test_rotate_an_agent_owned_flagged_key(tables):
    call("POST", "/agents/agent-owner/credentials", {"types": ["api_key"]})
    call("POST", "/flags", {"secret_refs": ["burn/agent-owner/api_key"], "level": "seen",
                            "evidence": {"source": "handoff", "agent_id": "agent-peer"}})
    status, body = call("POST", "/flags/resolve", {"secret_ref": "burn/agent-owner/api_key", "action": "rotate",
                                                   "reason": "peer saw it in a message"})
    assert status == 200 and body["rotated_to_version"] == 2


@pytest.mark.parametrize("reason", [None, "", "no", "x" * 301, "tab\x07bell"])
def test_resolution_needs_a_real_reason(shared_ref, reason):
    flag("seen", "burn", agent_id="agent-a")
    assert resolve("dismiss", reason=reason)[0] == 400


def test_reason_cannot_carry_a_registered_credential(shared_ref, secrets_client, tables):
    flag("seen", "burn", agent_id="agent-a")
    value = secrets_client.get_secret_value(SecretId=REF)["SecretString"]
    status, body = resolve("dismiss", reason=f"fine, the value is {value} anyway")
    assert status == 400 and "registered credential" in body["error"]
    assert value not in json.dumps(row(tables), default=str)


def test_bad_resolutions(shared_ref):
    assert resolve("dismiss")[0] == 404  # not flagged yet
    flag("seen", "burn", agent_id="agent-a")
    assert resolve("delete")[0] == 400
    assert resolve("dismiss", by="bad;by")[0] == 400
    assert call("POST", "/flags/resolve", {"secret_ref": "burn/agent-a/canary_" + "0" * 24, "action": "dismiss",
                                           "reason": "a canary is never flagged"})[0] == 404


def test_dismissing_a_flag_that_was_raised_meanwhile_is_a_conflict(shared_ref, monkeypatch):
    flag("seen", "burn", agent_id="agent-a")
    real = flags._flagged_row

    def raised_in_between(ref):
        snapshot = real(ref)
        flag("attempted", "egress", agent_id="agent-b")  # arrives while the owner is deciding
        return snapshot

    monkeypatch.setattr(flags, "_flagged_row", raised_in_between)
    assert resolve("dismiss")[0] == 409


def test_shared_credential_issued_without_template_cannot_be_rotated(tables):
    tables["credentials"].put_item(Item={"secret_ref": "burn/shared/legacy", "owner_agent_id": "shared",
                                         "secret_type": "other", "version": 1, "status": "active"})
    flags.raise_level("burn/shared/legacy", "seen", {"source": "burn"})
    status, body = call("POST", "/flags/resolve", {"secret_ref": "burn/shared/legacy", "action": "rotate",
                                                   "reason": "legacy row"})
    assert status == 400 and "re-issue" in body["error"]
