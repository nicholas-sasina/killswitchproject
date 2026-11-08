"""Delivery state: claims, history, correlation facts, aggregation windows, rate limits.

Two implementations with the same semantics:
  MemoryStore   local runs, the dry run and most tests (thread-safe)
  DynamoStore   deployed; one on-demand table `burn-notify-state` (pk, TTL on `ttl`)

Delivery item per (event_id, destination_id). A worker may only send after winning
an atomic claim:
  allowed when the item is new, or status is pending/retrying, or status is sending
  and its lease has expired (the previous worker died mid-send).
delivered / permanently_failed / suppressed are terminal; claims on them fail. That
is the deduplication for duplicate stream records and concurrent workers.

Residual risk (documented in the README): if a provider accepts a message and the
worker dies before mark_delivered, the lease expires and the message is sent again.
External delivery is at-least-once, never exactly-once.

Nothing stored here is a secret: no URLs, tokens, chat ids, provider bodies or raw
event detail. Credential references are kept only as a truncated SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

PENDING, SENDING, DELIVERED, RETRYING, FAILED, SUPPRESSED = (
    "pending", "sending", "delivered", "retrying", "permanently_failed", "suppressed")
TERMINAL = frozenset({DELIVERED, FAILED, SUPPRESSED})
FLAG_RANK = {"seen": 1, "attempted": 2, "reported": 3}
FACT_KINDS = ("revoked", "rotated", "flagged", "rotation_failed", "rotation_deferred")
DAY = 86400


def ref_hash(secret_ref: str) -> str:
    return hashlib.sha256(secret_ref.encode()).hexdigest()[:20]


@dataclass(frozen=True)
class Claim:
    won: bool
    status: str | None = None
    attempts: int = 0


@dataclass
class Facts:
    """What Burn has recorded so far about one agent's containment (set semantics)."""
    revoked: set[str] = field(default_factory=set)
    rotated: set[str] = field(default_factory=set)
    flagged: set[str] = field(default_factory=set)
    rotation_failed: set[str] = field(default_factory=set)
    rotation_deferred: set[str] = field(default_factory=set)


class Store:
    lease_seconds = 60

    # deliveries
    def claim(self, event_id: str, dest_id: str, provider: str, now: float, ttl_days: int = 14) -> Claim: ...
    def finish(self, event_id: str, dest_id: str, status: str, now: float, *, category: str | None = None,
               provider_ref: str | None = None, retry_after: float | None = None) -> None: ...
    def get(self, event_id: str, dest_id: str) -> dict | None: ...
    def list_deliveries(self, event_id: str | None = None) -> list[dict]: ...
    # ordering / correlation
    def add_fact(self, agent_id: str, kind: str, event_id: str, now: float) -> Facts: ...
    def facts(self, agent_id: str) -> Facts: ...
    def raise_flag(self, secret_ref: str, level: str, now: float) -> bool: ...
    # aggregation
    def window_add(self, dest_id: str, key: str, window_start: int, window_end: int, sample: dict,
                   now: float) -> int: ...
    def due_windows(self, now: float) -> list[dict]: ...
    def claim_window(self, window_pk: str, now: float) -> dict | None: ...
    def reopen_window(self, window_pk: str) -> None: ...
    # rate limiting
    def rate_take(self, dest_id: str, minute: int, limit: int, now: float) -> bool: ...


def delivery_pk(event_id: str, dest_id: str) -> str:
    return f"delivery#{event_id}#{dest_id}"


def window_pk(dest_id: str, key: str, window_start: int) -> str:
    return f"window#{dest_id}#{key}#{window_start}"


# ---------------------------------------------------------------------------------------------
class MemoryStore(Store):
    def __init__(self):
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def claim(self, event_id, dest_id, provider, now, ttl_days=14):
        pk = delivery_pk(event_id, dest_id)
        with self._lock:
            item = self._items.get(pk)
            if item is not None:
                status = item["status"]
                if status in TERMINAL or (status == SENDING and item["lease_until"] > now):
                    return Claim(False, status, item["attempts"])
            item = item or {"pk": pk, "event_id": event_id, "destination_id": dest_id, "provider": provider,
                            "attempts": 0, "first_attempt": now}
            item.update(status=SENDING, attempts=item["attempts"] + 1, last_attempt=now,
                        lease_until=now + self.lease_seconds, ttl=int(now + ttl_days * DAY))
            self._items[pk] = item
            return Claim(True, SENDING, item["attempts"])

    def finish(self, event_id, dest_id, status, now, *, category=None, provider_ref=None, retry_after=None):
        with self._lock:
            item = self._items[delivery_pk(event_id, dest_id)]
            item.update(status=status, last_attempt=now, lease_until=0)
            if category:
                item["failure_category"] = category
            if provider_ref:
                item["provider_ref"] = provider_ref
            if retry_after is not None:
                item["retry_after"] = retry_after

    def get(self, event_id, dest_id):
        with self._lock:
            item = self._items.get(delivery_pk(event_id, dest_id))
            return dict(item) if item else None

    def list_deliveries(self, event_id=None):
        with self._lock:
            rows = [dict(v) for k, v in self._items.items() if k.startswith("delivery#")]
        return sorted([r for r in rows if event_id in (None, r["event_id"])],
                      key=lambda r: (r["event_id"], r["destination_id"]))

    def add_fact(self, agent_id, kind, event_id, now):
        with self._lock:
            item = self._items.setdefault(f"facts#{agent_id}", {k: set() for k in FACT_KINDS})
            item[kind].add(event_id)
            return Facts(**{k: set(item[k]) for k in FACT_KINDS})

    def facts(self, agent_id):
        with self._lock:
            item = self._items.get(f"facts#{agent_id}")
            return Facts(**{k: set(item[k]) for k in FACT_KINDS}) if item else Facts()

    def raise_flag(self, secret_ref, level, now):
        rank = FLAG_RANK[level]
        with self._lock:
            item = self._items.setdefault(f"flag#{ref_hash(secret_ref)}", {"rank": 0})
            if rank < item["rank"]:
                return False
            item["rank"] = rank
            return True

    def window_add(self, dest_id, key, window_start, window_end, sample, now):
        pk = window_pk(dest_id, key, window_start)
        with self._lock:
            item = self._items.setdefault(pk, {"pk": pk, "destination_id": dest_id, "key": key, "count": 0,
                                               "window_start": window_start, "window_end": window_end,
                                               "flushed": False})
            item["count"] += 1
            item["sample"] = sample
            return item["count"]

    def due_windows(self, now):
        with self._lock:
            return [dict(v) for k, v in self._items.items()
                    if k.startswith("window#") and not v["flushed"] and v["window_end"] <= now]

    def claim_window(self, pk, now):
        with self._lock:
            item = self._items.get(pk)
            if not item or item["flushed"]:
                return None
            item["flushed"] = True
            return dict(item)

    def reopen_window(self, pk):
        with self._lock:
            if pk in self._items:
                self._items[pk]["flushed"] = False

    def rate_take(self, dest_id, minute, limit, now):
        with self._lock:
            item = self._items.setdefault(f"rate#{dest_id}#{minute}", {"n": 0})
            if item["n"] >= limit:
                return False
            item["n"] += 1
            return True


# ---------------------------------------------------------------------------------------------
class DynamoStore(Store):
    """Table: partition key `pk` (S), TTL attribute `ttl`, sparse GSI `due` (pk `due`, sk `window_end`)."""

    DUE_INDEX = "due"

    def __init__(self, table_name: str, *, resource=None, lease_seconds: int | None = None):
        if lease_seconds is not None:
            self.lease_seconds = lease_seconds  # must exceed the Lambda timeout (see handler.py)
        if resource is None:
            import boto3
            resource = boto3.resource("dynamodb")
        self.table = resource.Table(table_name)
        self._conditional = self.table.meta.client.exceptions.ConditionalCheckFailedException

    def claim(self, event_id, dest_id, provider, now, ttl_days=14):
        pk = delivery_pk(event_id, dest_id)
        try:
            out = self.table.update_item(
                Key={"pk": pk},
                UpdateExpression=("SET #s = :sending, event_id = :e, destination_id = :d, #prov = :p, "
                                  "last_attempt = :now, lease_until = :lease, #ttl = :ttl, "
                                  "first_attempt = if_not_exists(first_attempt, :now) ADD attempts :one"),
                ConditionExpression=("attribute_not_exists(pk) OR #s IN (:pending, :retrying) "
                                     "OR (#s = :sending AND lease_until < :now)"),
                ExpressionAttributeNames={"#s": "status", "#ttl": "ttl", "#prov": "provider"},
                ExpressionAttributeValues={":sending": SENDING, ":pending": PENDING, ":retrying": RETRYING,
                                           ":e": event_id, ":d": dest_id, ":p": provider, ":one": 1,
                                           ":now": _num(now), ":lease": _num(now + self.lease_seconds),
                                           ":ttl": int(now + ttl_days * DAY)},
                ReturnValues="ALL_NEW")
            return Claim(True, SENDING, int(out["Attributes"]["attempts"]))
        except self._conditional:
            item = self.get(event_id, dest_id) or {}
            return Claim(False, item.get("status"), int(item.get("attempts", 0)))

    def finish(self, event_id, dest_id, status, now, *, category=None, provider_ref=None, retry_after=None):
        sets = {"#s": status, "last_attempt": _num(now), "lease_until": 0}
        if category:
            sets["failure_category"] = category
        if provider_ref:
            sets["provider_ref"] = provider_ref
        if retry_after is not None:
            sets["retry_after"] = _num(retry_after)
        names = {"#s": "status"}
        values, parts = {}, []
        for i, (attr, value) in enumerate(sets.items()):
            values[f":v{i}"] = value
            parts.append(f"{attr} = :v{i}")
        self.table.update_item(Key={"pk": delivery_pk(event_id, dest_id)},
                               UpdateExpression="SET " + ", ".join(parts),
                               ExpressionAttributeNames=names, ExpressionAttributeValues=values)

    def get(self, event_id, dest_id):
        item = self.table.get_item(Key={"pk": delivery_pk(event_id, dest_id)}, ConsistentRead=True).get("Item")
        return _plain(item) if item else None

    def list_deliveries(self, event_id=None):
        from boto3.dynamodb.conditions import Attr
        prefix = f"delivery#{event_id}#" if event_id else "delivery#"
        kwargs = {"FilterExpression": Attr("pk").begins_with(prefix)}
        rows = []
        while True:
            page = self.table.scan(**kwargs)
            rows += [_plain(i) for i in page["Items"]]
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return sorted(rows, key=lambda r: (r["event_id"], r["destination_id"]))

    def add_fact(self, agent_id, kind, event_id, now):
        out = self.table.update_item(
            Key={"pk": f"facts#{agent_id}"}, UpdateExpression="ADD #k :e SET #ttl = :ttl",
            ExpressionAttributeNames={"#k": kind, "#ttl": "ttl"},
            ExpressionAttributeValues={":e": {event_id}, ":ttl": int(now + DAY)}, ReturnValues="ALL_NEW")
        return _facts(out["Attributes"])

    def facts(self, agent_id):
        item = self.table.get_item(Key={"pk": f"facts#{agent_id}"}, ConsistentRead=True).get("Item")
        return _facts(item or {})

    def raise_flag(self, secret_ref, level, now):
        try:
            self.table.update_item(
                Key={"pk": f"flag#{ref_hash(secret_ref)}"}, UpdateExpression="SET #r = :r, #ttl = :ttl",
                ConditionExpression="attribute_not_exists(#r) OR #r <= :r",
                ExpressionAttributeNames={"#r": "rank", "#ttl": "ttl"},
                ExpressionAttributeValues={":r": FLAG_RANK[level], ":ttl": int(now + 30 * DAY)})
            return True
        except self._conditional:
            return False

    def window_add(self, dest_id, key, window_start, window_end, sample, now):
        out = self.table.update_item(
            Key={"pk": window_pk(dest_id, key, window_start)},
            UpdateExpression=("ADD #c :one SET destination_id = :d, #k = :k, window_start = :ws, "
                              "window_end = :we, #sample = :s, #due = if_not_exists(#due, :due), #ttl = :ttl"),
            ExpressionAttributeNames={"#c": "count", "#k": "key", "#ttl": "ttl", "#sample": "sample", "#due": "due"},
            ExpressionAttributeValues={":one": 1, ":d": dest_id, ":k": key, ":ws": window_start, ":we": window_end,
                                       ":s": json.dumps(sample), ":due": "pending", ":ttl": int(now + 2 * DAY)},
            ReturnValues="ALL_NEW")
        return int(out["Attributes"]["count"])

    def due_windows(self, now):
        from boto3.dynamodb.conditions import Key
        rows, kwargs = [], {"IndexName": self.DUE_INDEX,
                            "KeyConditionExpression": Key("due").eq("pending") & Key("window_end").lte(int(now))}
        while True:
            page = self.table.query(**kwargs)
            rows += page["Items"]
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return [_window(i) for i in rows]

    def claim_window(self, pk, now):
        try:
            out = self.table.update_item(
                Key={"pk": pk}, UpdateExpression="REMOVE #due SET flushed_at = :now",
                ConditionExpression="#due = :pending", ExpressionAttributeNames={"#due": "due"},
                ExpressionAttributeValues={":pending": "pending", ":now": _num(now)}, ReturnValues="ALL_NEW")
        except self._conditional:
            return None
        return _window(out["Attributes"])

    def reopen_window(self, pk):
        self.table.update_item(Key={"pk": pk}, UpdateExpression="SET #due = :pending",
                               ConditionExpression="attribute_exists(pk)", ExpressionAttributeNames={"#due": "due"},
                               ExpressionAttributeValues={":pending": "pending"})

    def rate_take(self, dest_id, minute, limit, now):
        try:
            self.table.update_item(
                Key={"pk": f"rate#{dest_id}#{minute}"}, UpdateExpression="ADD #n :one SET #ttl = :ttl",
                ConditionExpression="attribute_not_exists(#n) OR #n < :limit",
                ExpressionAttributeNames={"#ttl": "ttl", "#n": "n"},
                ExpressionAttributeValues={":one": 1, ":limit": limit, ":ttl": int(now + 600)})
            return True
        except self._conditional:
            return False


def _num(value: float):
    from decimal import Decimal
    return Decimal(str(round(value, 3)))


def _plain(item: dict) -> dict:
    from decimal import Decimal
    return {k: (float(v) if isinstance(v, Decimal) and v % 1 else int(v) if isinstance(v, Decimal) else v)
            for k, v in item.items()}


def _facts(item: dict) -> Facts:
    return Facts(**{k: set(item.get(k) or ()) for k in FACT_KINDS})


def _window(item: dict) -> dict:
    row = _plain(item)
    row["sample"] = json.loads(row["sample"]) if isinstance(row.get("sample"), str) else row.get("sample")
    row["flushed"] = "due" not in row
    return row


def now() -> float:
    return time.time()
