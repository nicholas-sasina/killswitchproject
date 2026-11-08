"""Fingerprint-cache behavior: one paginated refresh, then in-memory lookups."""
import detection


class FakeTable:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs.copy())
        return self.pages.pop(0)


class FakeDynamo:
    def __init__(self, table):
        self._table = table

    def Table(self, name):
        assert name == detection.FINGERPRINTS_TABLE
        return self._table


def test_lookup_uses_one_snapshot_for_many_candidate_sets(monkeypatch):
    row = {"fingerprint": "a" * 64, "secret_ref": "burn/agent-alpha/api_key", "kind": "exact"}
    table = FakeTable([{"Items": [row]}])
    monkeypatch.setattr(detection, "_dynamodb", lambda: FakeDynamo(table))
    detection.reset_cache()

    assert detection.lookup({"a" * 64}) == {"a" * 64: row}
    assert detection.lookup({"b" * 64, "a" * 64}) == {"a" * 64: row}
    assert len(table.calls) == 1
    assert table.calls[0] == {"ConsistentRead": True}


def test_refresh_paginates_and_ttl_refreshes(monkeypatch):
    first = {"fingerprint": "a" * 64, "version": 1}
    second = {"fingerprint": "b" * 64, "version": 2}
    table = FakeTable([
        {"Items": [first], "LastEvaluatedKey": {"fingerprint": "a" * 64}},
        {"Items": [second]},
        {"Items": [{**first, "version": 3}]},
    ])
    monkeypatch.setattr(detection, "_dynamodb", lambda: FakeDynamo(table))
    clock = [100.0]
    monkeypatch.setattr(detection.time, "monotonic", lambda: clock[0])
    detection.reset_cache()

    assert detection.lookup({"a" * 64, "b" * 64}) == {
        "a" * 64: first, "b" * 64: second
    }
    assert len(table.calls) == 2
    assert table.calls[0]["ConsistentRead"] is True
    assert "ExclusiveStartKey" in table.calls[1]

    # A warm cache avoids reads until its short TTL expires.
    clock[0] += detection._CACHE_TTL_SECONDS - 0.1
    assert detection.lookup({"a" * 64})["a" * 64]["version"] == 1
    assert len(table.calls) == 2

    # The next lookup refreshes and sees the updated row.
    clock[0] += 0.2
    assert detection.lookup({"a" * 64})["a" * 64]["version"] == 3
    assert len(table.calls) == 3


def test_explicit_refresh_reloads_cache(monkeypatch):
    table = FakeTable([
        {"Items": [{"fingerprint": "c" * 64, "version": 1}]},
        {"Items": [{"fingerprint": "c" * 64, "version": 2}]},
    ])
    monkeypatch.setattr(detection, "_dynamodb", lambda: FakeDynamo(table))
    detection.reset_cache()

    assert detection.refresh_cache() == 1
    assert detection.lookup({"c" * 64})["c" * 64]["version"] == 1
    assert detection.refresh_cache() == 1
    assert detection.lookup({"c" * 64})["c" * 64]["version"] == 2
    assert len(table.calls) == 2
