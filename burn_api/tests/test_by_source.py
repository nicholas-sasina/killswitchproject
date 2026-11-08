"""POST /burn/by-source: burn every instance that read a source, and whoever they messaged."""
import boto3
import pytest

from conftest import call
from test_swarm import WORKER, make_role

SOURCE = "read_ticket:T-42"


@pytest.fixture
def ledger(env):
    """burn-exposures with the by_source index (PK source_ref, SK first_seen), as infra creates it."""
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.delete_table(TableName="burn-exposures")
    client.create_table(
        TableName="burn-exposures", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"}
                              for a in ("agent_id", "secret_ref_or_fp", "source_ref", "first_seen")],
        KeySchema=[{"AttributeName": "agent_id", "KeyType": "HASH"},
                   {"AttributeName": "secret_ref_or_fp", "KeyType": "RANGE"}],
        GlobalSecondaryIndexes=[{"IndexName": "by_source", "Projection": {"ProjectionType": "ALL"},
                                 "KeySchema": [{"AttributeName": "source_ref", "KeyType": "HASH"},
                                               {"AttributeName": "first_seen", "KeyType": "RANGE"}]}])
    return env.ddb.Table("burn-exposures")


def row(table, agent_id, ref, *, source_ref, first_seen, source="tool_response", via="read_ticket"):
    """A ledger row the way the Gateway writes it (record.py / exit_check.py)."""
    item = {"agent_id": agent_id, "secret_ref_or_fp": ref, "secret_ref": ref, "fingerprint": "a" * 64,
            "secret_type": "other", "owner": "shared", "source": source, "via": via,
            "first_seen": first_seen, "status": "seen"}
    if source_ref:
        item["source_ref"] = source_ref
    table.put_item(Item=item)


def instances(env, n):
    make_role(env, WORKER)
    token = call(env, "POST", "/roles/worker/tokens", {"uses": n})[1]["token"]
    return [call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token})[1] for _ in range(n)]


def status_of(env, session_id):
    return call(env, "GET", f"/badge/{session_id}")[1]["status"]


def test_burns_readers_including_an_exited_one_and_whoever_they_messaged(env, ledger):
    reader, messaged, bystander, exited = instances(env, 4)
    shared = "burn/shared/crm_db_url"
    row(ledger, reader["agent_id"], shared, source_ref=SOURCE, first_seen="2026-11-07T20:00:00.000Z")
    row(ledger, exited["agent_id"], shared, source_ref=SOURCE, first_seen="2026-11-07T20:01:00.000Z")
    row(ledger, messaged["agent_id"], shared, source_ref=SOURCE, first_seen="2026-11-07T20:05:00.000Z",
        source="handoff", via=reader["agent_id"])  # send_message copied the reader's row
    row(ledger, bystander["agent_id"], shared, source_ref="read_ticket:T-7", first_seen="2026-11-07T20:00:00.000Z")
    assert call(env, "POST", f"/instances/{exited['session_id']}/exit")[0] == 200

    status, out = call(env, "POST", "/burn/by-source", {"source_ref": SOURCE, "reason": "poisoned ticket"})
    assert status == 200 and out["failed"] == []
    assert {(r["agent_id"], r["via"]) for r in out["readers"]} == {
        (reader["agent_id"], "read"), (exited["agent_id"], "read"), (messaged["agent_id"], "handoff")}
    assert {b["agent_id"] for b in out["burned"]} == {reader["agent_id"], exited["agent_id"], messaged["agent_id"]}
    assert status_of(env, reader["session_id"]) == status_of(env, messaged["session_id"]) == "revoked"
    assert status_of(env, exited["session_id"]) == "exited"  # already gone; burned all the same
    assert status_of(env, bystander["session_id"]) == "active"
    rotated = {c[1] for c in env.minter.calls if c[1].endswith("/rotate")}
    assert f"/agents/{exited['agent_id']}/rotate" in rotated
    assert f"/agents/{bystander['agent_id']}/rotate" not in rotated


def test_since_limits_the_window(env, ledger):
    early, late = instances(env, 2)
    row(ledger, early["agent_id"], "burn/shared/x", source_ref=SOURCE, first_seen="2026-11-07T19:00:00.000Z")
    row(ledger, late["agent_id"], "burn/shared/x", source_ref=SOURCE, first_seen="2026-11-07T21:00:00.000Z")
    out = call(env, "POST", "/burn/by-source", {"source_ref": SOURCE, "since": "2026-11-07T20:00:00Z"})[1]
    assert [b["agent_id"] for b in out["burned"]] == [late["agent_id"]]
    assert out["since"] == "2026-11-07T20:00:00.000Z"
    assert status_of(env, early["session_id"]) == "active"


def test_tool_and_resource_id_can_be_sent_separately(env, ledger):
    [one] = instances(env, 1)
    row(ledger, one["agent_id"], "burn/shared/x", source_ref=SOURCE, first_seen="2026-11-07T20:00:00.000Z")
    out = call(env, "POST", "/burn/by-source", {"tool": "read_ticket", "source_ref": "T-42"})[1]
    assert out["source_ref"] == SOURCE and [b["agent_id"] for b in out["burned"]] == [one["agent_id"]]


def test_nobody_read_it(env, ledger):
    assert call(env, "POST", "/burn/by-source", {"source_ref": "read_ticket:T-0"})[0] == 404


@pytest.mark.parametrize("body", [
    {}, {"source_ref": ""}, {"source_ref": "x" * 261}, {"source_ref": "a\nb"},
    {"source_ref": SOURCE, "since": "yesterday"}, {"source_ref": "T-1", "tool": "Bad Tool"},
])
def test_bad_requests(env, ledger, body):
    assert call(env, "POST", "/burn/by-source", body)[0] == 400
