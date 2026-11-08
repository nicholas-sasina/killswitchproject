"""Owner review of flagged credentials: GET /flags and POST /flags/resolve.

One flag per credential (the minter keeps it on the credential's row, with the
highest level reached and how many instances saw it). An owner either rotates the
credential (the minter mints a new version that agents get through placeholders)
or dismisses the flag; both need a reason, which goes into the event feed. The
minter refuses a reason that contains a registered credential.
"""
from burn_contracts import names

import db
import minter_client


def list_flags() -> dict:
    return minter_client.call("GET", "/flags")


def resolve(body: dict) -> tuple[int, dict]:
    request = {k: body.get(k) for k in ("secret_ref", "action", "reason", "by")}
    try:
        result = minter_client.call("POST", "/flags/resolve", request)
    except minter_client.MinterError as exc:
        if exc.status in (400, 404, 409):
            return exc.status, {"error": exc.detail or "invalid request"}
        raise
    flag = result["flag"]
    decision = flag["resolution"]
    who = decision.get("by") or "owner"
    owner = flag.get("owner_agent_id")
    ids = {"secret_ref": flag["secret_ref"], "secret_type": flag.get("secret_type"),
           "owner": "shared" if owner == names.SHARED_OWNER else "agent",
           "owner_agent_id": None if owner == names.SHARED_OWNER else owner}
    if decision["action"] == "rotate":
        db.write_event(agent_id="system", source="burn", action="rotated", **ids,
                       detail=f"{who} rotated this flagged credential (was {decision['level']}, "
                              f"{flag['instance_count']} instance(s)): {decision['reason']}"[:500])
    else:
        db.write_event(agent_id="system", source="burn", action="dismissed", flag_level=decision["level"], **ids,
                       detail=f"dismissed by {who} ({flag['instance_count']} instance(s)): {decision['reason']}"[:500])
    return 200, result
