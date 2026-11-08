"""Fingerprint cache: DynamoDB work per call stays flat as tool output grows."""
import secrets
import string
from collections import Counter

import pytest
from botocore.client import BaseClient

import detection
import interceptor
from conftest import (bump_version, ledger_rows, put_session, register_credential, response_event, tool_call,
                      tool_result)

SIZES = {"36 B": 36, "1 KiB": 1024, "16 KiB": 16384}


@pytest.fixture
def ddb_calls(monkeypatch):
    """Every DynamoDB operation and how many keys/items it asked for."""
    calls = Counter()
    real = BaseClient._make_api_call

    def counting(self, operation, params):
        if self.meta.service_model.service_name == "dynamodb":
            calls[operation] += 1
            if operation == "BatchGetItem":
                calls["BatchGetItem keys"] += sum(len(v["Keys"]) for v in params["RequestItems"].values())
        return real(self, operation, params)

    monkeypatch.setattr(BaseClient, "_make_api_call", counting)
    return calls


def output_of(size: int, value: str) -> str:
    words = " ".join("".join(secrets.choice(string.ascii_lowercase) for _ in range(7)) for _ in range(size // 8 + 1))
    return (f"KEY={value} " + words)[:max(size, len(value) + 5)]


def respond(session, text, rpc_id=1):
    body = tool_call("read_config", {"path": "config/app.env"}, rpc_id)
    return interceptor.handler(response_event(body, session["client_id"], tool_result(text, rpc_id),
                                              trace=f"Root=1-{rpc_id}"), None)


def per_call_reads(aws, ddb_calls, value, session):
    respond(session, "warm-up", rpc_id=999)  # load the cache once
    reads = {}
    for i, (label, size) in enumerate(SIZES.items()):
        ddb_calls.clear()
        respond(session, output_of(size, value), rpc_id=i + 1)
        reads[label] = {op: n for op, n in ddb_calls.items() if op in ("GetItem", "Query", "Scan", "BatchGetItem",
                                                                        "BatchGetItem keys")}
    return reads


def test_reads_per_call_are_flat_across_output_sizes(aws, ddb_calls):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")
    reads = per_call_reads(aws, ddb_calls, value, session)
    print(f"\ncached reads per call: {reads}")
    assert reads["36 B"] == reads["1 KiB"] == reads["16 KiB"]
    assert "BatchGetItem" not in reads["16 KiB"] and "Scan" not in reads["16 KiB"]
    assert all(row["secret_ref"] for row in ledger_rows(aws, "agent-alpha").values())


def test_without_a_version_item_lookups_grow_with_output_as_before(aws, ddb_calls):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha", bump=False)
    reads = per_call_reads(aws, ddb_calls, value, session)
    print(f"\nfallback reads per call: {reads}")
    assert reads["16 KiB"]["BatchGetItem keys"] > 10 * reads["36 B"]["BatchGetItem keys"]
    assert "burn/agent-alpha/api_key" in ledger_rows(aws, "agent-alpha")  # still correct


def test_a_credential_minted_after_loading_is_recognized_on_the_next_call(aws):
    session = put_session(aws)
    register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")
    respond(session, "nothing here", rpc_id=1)  # cache loaded
    fresh = register_credential(aws, "burn/shared/new_token", "shared", secret_type="other")
    respond(session, f"token: {fresh}", rpc_id=2)
    assert "burn/shared/new_token" in ledger_rows(aws, "agent-alpha")


def test_the_set_is_reloaded_only_when_the_version_changes(aws, ddb_calls):
    session = put_session(aws)
    register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")
    for i in range(5):
        respond(session, f"call {i}", rpc_id=i + 1)
    assert ddb_calls["Scan"] == 1
    bump_version(aws)
    respond(session, "after bump", rpc_id=10)
    assert ddb_calls["Scan"] == 2


def test_rotation_status_is_seen_after_the_bump(aws):
    register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")
    assert detection.cached_rows()
    table = aws.Table("burn-fingerprints")
    for row in table.scan()["Items"]:
        if row["fingerprint"] != "_version":
            table.put_item(Item={**row, "status": "rotated"})
    bump_version(aws)
    assert {r["status"] for r in detection.cached_rows().values()} == {"rotated"}


def test_an_unreadable_cache_falls_back_to_direct_lookups(aws, monkeypatch):
    session = put_session(aws)
    value = register_credential(aws, "burn/agent-alpha/api_key", "agent-alpha")

    def denied(**kwargs):
        raise detection._dynamodb().meta.client.exceptions.ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no Scan"}}, "Scan")

    monkeypatch.setattr(detection, "_load_all", lambda: denied())
    assert detection.cached_rows() is None
    respond(session, f"KEY={value}")
    assert "burn/agent-alpha/api_key" in ledger_rows(aws, "agent-alpha")
