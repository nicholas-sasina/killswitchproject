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
    assert (status, body) == (200, {"flags": [{"secret_ref": REF, "flag_level": "seen", "changed": True}]})
    r = row(tables)
    assert (r["status"], r["flag_level"], r["flagged_by"]) == ("flagged", "seen", "agent-a")
    ev = r["flag_evidence_seen"]
    assert (ev["source"], ev["event_id"], ev["session_id"], ev["tool"]) == ("tool_response", evt, "sess_1", "read_config")


def test_blocked_outbound_attempt_is_attempted(shared_ref, tables):  # 18
    flag("seen", "tool_response", agent_id="agent-a")
    status, body = flag("attempted", "egress", agent_id="agent-a", event_id=new_event_id())
    assert body["flags"][0] == {"secret_ref": REF, "flag_level": "attempted", "changed": True}
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
        assert body["flags"][0] == {"secret_ref": REF, "flag_level": "reported", "changed": False}
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
