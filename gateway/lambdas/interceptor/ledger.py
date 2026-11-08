"""The exposure ledger: which credentials each agent has seen (burn-exposures).

One row per (agent_id, credential). Rows hold fingerprints and references,
never values. When Burn burns an agent, this is the list of everything that
must be rotated or flagged.

Every sighting updates last_seen and seen_in, but only the first one per
(agent, credential) is reported as new: an agent that reads the same file or
gets the same sender's messages again already has that entry. The write is
keyed by the call that caused it (trace id + JSON-RPC id), so an interceptor
retry finds its call already in seen_in and changes nothing.
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


def record(agent_id: str, finding: Finding, *, source: str, via: str, call: str,
           status: str = "seen", source_ref: str | None = None, report: str | None = None) -> bool:
    """Add the credential to the agent's ledger. True only if the agent did not have it yet.

    status: "referenced" if the agent only got {{burn:ref:...}}, "seen" if the plaintext reached it.
    source_ref: what it was read from ('<tool>:<resource>'), kept from the first sighting.
    report: in report mode, what replace mode would have done (latest call).
    """
    return _upsert(agent_id, ledger_key(finding), {
        "secret_ref": finding.secret_ref,
        "fingerprint": finding.fingerprint,
        "secret_type": finding.secret_type,
        "owner": finding.owner,
        "owner_agent_id": finding.owner_agent_id,
        "source_ref": source_ref,
    }, source=source, via=via, call=call, status=status, report=report)


def entries(agent_id: str) -> list[dict]:
    items, kwargs = [], {"KeyConditionExpression": Key("agent_id").eq(agent_id), "ConsistentRead": True}
    while True:
        page = _table().query(**kwargs)
        items += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def copy_entry(entry: dict, to_agent: str, *, via: str, call: str) -> bool:
    """Give to_agent the sender's ledger entry (it received a message from them).
    True only if to_agent did not have it yet."""
    fields = {k: entry.get(k) for k in ("secret_ref", "fingerprint", "secret_type", "owner", "owner_agent_id",
                                        "source_ref")}
    # A sender that only held a reference cannot pass on the value; source_ref carries over so
    # "burn everyone who read this" also reaches who they messaged.
    return _upsert(to_agent, entry["secret_ref_or_fp"], fields, source="handoff", via=via, call=call,
                   status=entry.get("status") or "seen")


def _upsert(agent_id: str, key: str, fields: dict, *, source: str, via: str, call: str,
            status: str = "seen", report: str | None = None) -> bool:
    # The first sighting decides source/via/first_seen/source_ref; later ones only add to seen_in.
    first = {**fields, "source": source, "via": via, "first_seen": utc_now_iso()}
    if first.get("source_ref") is None:
        # source_ref is a key of the by_source index: a NULL there would fail the whole write.
        first.pop("source_ref", None)
    names = {f"#{k}": k for k in first}
    values = {f":{k}": v for k, v in first.items()}
    sets = [f"#{k} = if_not_exists(#{k}, :{k})" for k in first]
    # "seen" wins for good once plaintext reached the agent; "referenced" only fills a new row.
    names["#status"], values[":status"] = "status", status
    sets.append("#status = :status" if status == "seen" else "#status = if_not_exists(#status, :status)")
    if report is not None:
        names["#report"], values[":report"] = "report", report
        sets.append("#report = :report")
    try:
        response = _table().update_item(
            Key={"agent_id": agent_id, "secret_ref_or_fp": key},
            UpdateExpression=f"SET {', '.join(sets)}, last_seen = :now ADD seen_in :calls",
            ConditionExpression="attribute_not_exists(seen_in) OR NOT contains(seen_in, :call)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={**values, ":now": utc_now_iso(), ":calls": {call}, ":call": call},
            ReturnValues="ALL_OLD",
        )
    except _table().meta.client.exceptions.ConditionalCheckFailedException:
        return False  # interceptor retry: this call is already in seen_in
    # ALL_OLD returns the entry as it was before this write: nothing means it is new.
    return not response.get("Attributes")
