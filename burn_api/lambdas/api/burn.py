"""POST /burn: contain one agent.

1. Revoke its sessions first. This is the part that must never fail.
2. Read what the agent saw (its ledger) and ask the minter to rotate its own
   credentials and flag the shared ones.
3. Record each outcome as an event for the dashboard.
"""
from boto3.dynamodb.conditions import Key
from burn_contracts import names

import db
import minter_client
import sessions


def _exposed_refs(agent_id: str) -> list[str]:
    rows = db.query_all(
        db.table(names.EXPOSURES_TABLE), KeyConditionExpression=Key("agent_id").eq(agent_id)
    )
    # Unknown-format credentials have no secret_ref; there is nothing to rotate for them.
    return sorted({r["secret_ref"] for r in rows if r.get("secret_ref")})


def burn_agent(agent_id: str, reason: str = "") -> dict:
    if db.get_agent(agent_id) is None:
        raise sessions.NotFound(agent_id)

    revoked_ids = sessions.revoke_sessions(agent_id)
    reason_text = f" ({reason[:200]})" if reason else ""
    db.write_event(
        agent_id=agent_id, source="burn", action="revoked",
        session_id=revoked_ids[-1] if revoked_ids else None,
        detail=f"{len(revoked_ids)} session(s) revoked{reason_text}",
    )

    result = minter_client.call(
        "POST", f"/agents/{agent_id}/rotate", {"exposed_refs": _exposed_refs(agent_id)}
    )
    rotated, flagged = result.get("rotated", []), result.get("flagged", [])

    for ref in rotated:
        db.write_event(agent_id=agent_id, source="burn", action="rotated", secret_ref=ref,
                       owner="agent", owner_agent_id=agent_id,
                       detail="credential rotated; previous value no longer accepted")
    for ref in flagged:
        owner = names.owner_of(ref)
        db.write_event(agent_id=agent_id, source="burn", action="flagged", secret_ref=ref,
                       owner="shared" if owner == names.SHARED_OWNER else "agent",
                       owner_agent_id=None if owner == names.SHARED_OWNER else owner,
                       detail="agent saw this credential; it is not the agent's own, review it")

    return {
        "agent_id": agent_id,
        # After a burn the agent has no usable badge, whether or not it had a session.
        "revoked": True,
        "sessions_revoked": revoked_ids,
        "rotated": rotated,
        "flagged": flagged,
    }
