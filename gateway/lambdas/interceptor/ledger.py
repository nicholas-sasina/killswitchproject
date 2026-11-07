"""The exposure ledger: which credentials each agent has seen (burn-exposures).

One row per (agent_id, credential). Rows hold fingerprints and references,
never values. When Burn burns an agent, this is the list of everything that
must be rotated or flagged.

The interceptor may be retried, so every write is keyed by the call that
caused it (trace id + JSON-RPC id): a retry finds its call already in
seen_in and records nothing twice.
"""
import functools

import boto3
from boto3.dynamodb.conditions import Key
from burn_contracts.events import utc_now_iso
from burn_contracts.names import EXPOSURES_TABLE, REGION
from burn_detect import Finding

from detection import ledger_key


@functools.cache
def _table():
    return boto3.resource("dynamodb", region_name=REGION).Table(EXPOSURES_TABLE)


def record(agent_id: str, finding: Finding, *, source: str, via: str, call: str) -> bool:
    """Add the credential to the agent's ledger. False if this call already recorded it."""
    return _upsert(agent_id, ledger_key(finding), {
        "secret_ref": finding.secret_ref,
        "fingerprint": finding.fingerprint,
        "secret_type": finding.secret_type,
        "owner": finding.owner,
        "owner_agent_id": finding.owner_agent_id,
    }, source=source, via=via, call=call)


def entries(agent_id: str) -> list[dict]:
    items, kwargs = [], {"KeyConditionExpression": Key("agent_id").eq(agent_id), "ConsistentRead": True}
    while True:
        page = _table().query(**kwargs)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def copy_entry(entry: dict, to_agent: str, *, via: str, call: str) -> bool:
    """Give to_agent the sender's ledger entry (it received a message from them)."""
    fields = {k: entry.get(k) for k in ("secret_ref", "fingerprint", "secret_type", "owner", "owner_agent_id")}
    return _upsert(to_agent, entry["secret_ref_or_fp"], fields, source="handoff", via=via, call=call)


def _upsert(agent_id: str, key: str, fields: dict, *, source: str, via: str, call: str) -> bool:
    # The first sighting decides source/via/first_seen; later ones only add to seen_in.
    first = {**fields, "source": source, "via": via, "first_seen": utc_now_iso()}
    names = {f"#{k}": k for k in first}
    values = {f":{k}": v for k, v in first.items()}
    sets = ", ".join(f"#{k} = if_not_exists(#{k}, :{k})" for k in first)
    try:
        _table().update_item(
            Key={"agent_id": agent_id, "secret_ref_or_fp": key},
            UpdateExpression=f"SET {sets}, last_seen = :now ADD seen_in :calls",
            ConditionExpression="attribute_not_exists(seen_in) OR NOT contains(seen_in, :call)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={**values, ":now": utc_now_iso(), ":calls": {call}, ":call": call},
        )
    except _table().meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True
