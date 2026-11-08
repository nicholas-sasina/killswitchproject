"""Read-only routes: events feed, ledger, badge status, blast radius."""
from boto3.dynamodb.conditions import Key
from burn_contracts import names

import db
import minter_client
import sessions

CLOUDTRAIL_NOTE = "CloudTrail event history can lag 5-15 minutes behind real activity."
MAX_LIMIT = 500


def _exposure_count(agent_id: str) -> int:
    resp = db.table(names.EXPOSURES_TABLE).query(
        KeyConditionExpression=Key("agent_id").eq(agent_id), Select="COUNT"
    )
    return resp["Count"]


def agent_states() -> list[dict]:
    meta = db.table(names.SCENARIO_TABLE).get_item(Key={"pk": "meta"}).get("Item")
    states = []
    for agent_id in (meta["data"].get("agent_ids", []) if meta else []):
        agent = db.get_agent(agent_id) or {}
        mine = db.sessions_for_agent(agent_id)
        latest = max(mine, key=lambda s: s.get("created_at", ""), default=None)
        states.append({
            "agent_id": agent_id,
            "role": agent.get("role"),
            "status": latest["status"] if latest else "idle",
            "session_id": latest["session_id"] if latest else None,
            "exposures": _exposure_count(agent_id),
        })
    return states


def events_feed(since: str | None, limit: int) -> dict:
    limit = max(1, min(limit, MAX_LIMIT))
    tbl = db.table(names.EVENTS_TABLE)
    stream = Key("stream").eq(names.EVENTS_STREAM)
    if since:
        items = tbl.query(KeyConditionExpression=stream & Key("event_id").gt(since), Limit=limit)["Items"]
    else:
        # Newest `limit` events, then flipped back to ascending order.
        items = tbl.query(KeyConditionExpression=stream, ScanIndexForward=False, Limit=limit)["Items"][::-1]
    events = [{k: v for k, v in item.items() if k != "stream"} for item in items]
    return {
        "events": events,
        "agents": agent_states(),
        "cursor": events[-1]["event_id"] if events else since,
    }


def ledger(agent_id: str) -> dict:
    rows = db.query_all(
        db.table(names.EXPOSURES_TABLE), KeyConditionExpression=Key("agent_id").eq(agent_id)
    )
    creds = db.table(names.CREDENTIALS_TABLE)
    entries = []
    for row in rows:
        ref = row.get("secret_ref")
        status, flag_level = "active", None
        if ref:
            cred = creds.get_item(Key={"secret_ref": ref}).get("Item") or {}
            if cred.get("status") in ("active", "rotated", "flagged"):
                status = cred["status"]
            flag_level = cred.get("flag_level") if status == "flagged" else None
        entries.append({
            "secret_ref": ref,
            "fingerprint": row.get("fingerprint") or row["secret_ref_or_fp"].removeprefix("fp#"),
            "secret_type": row.get("secret_type", "other"),
            "owner": row.get("owner", "unknown"),
            "owner_agent_id": row.get("owner_agent_id"),
            "source": row.get("source", "tool_response"),
            "via": row.get("via"),
            "first_seen": row.get("first_seen"),
            "status": status,
            "flag_level": flag_level,
        })
    entries.sort(key=lambda e: e["first_seen"] or "")
    return {"agent_id": agent_id, "entries": entries}


def badge(session_id: str) -> dict:
    item = db.table(names.SESSIONS_TABLE).get_item(Key={"session_id": session_id}).get("Item")
    if not item:
        raise sessions.NotFound(session_id)
    return {"session_id": session_id, "agent_id": item["agent_id"], "status": item["status"]}


def blast_radius(agent_id: str, since: str | None = None) -> dict:
    result = minter_client.call("GET", f"/agents/{agent_id}/key-activity", query={"since": since} if since else None)
    # The minter may return a bare list of events or {"keys": [...], "events": [...]}.
    events = result if isinstance(result, list) else result.get("events", [])
    keys = [] if isinstance(result, list) else result.get("keys", [])
    if not keys:
        keys = _keys_from_events(events)
    return {"agent_id": agent_id, "keys": keys, "events": events, "note": CLOUDTRAIL_NOTE}


def _keys_from_events(events: list[dict]) -> list[dict]:
    """Derive the key list from activity: the highest version is current, older ones are inactive."""
    seen = {}
    for e in events:
        seen.setdefault(e["key_ref"], e.get("key_hint"))
    newest = max(seen, key=lambda ref: int(ref.rsplit("#v", 1)[-1] or 0), default=None)
    return [{"key_ref": ref, "key_hint": hint, "status": "active" if ref == newest else "inactive"}
            for ref, hint in sorted(seen.items())]
