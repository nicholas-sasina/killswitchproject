"""A flagged shared credential was presented at the provider: raise it to `reported`.

Shared keys have legitimate users, so a presentation alone proves nothing. But
once a shared credential is flagged (a suspect agent saw it, or tried to send it
out), seeing it used at a monitored service is the strongest evidence a reviewer
can get. The flag logic itself lives in the minter (POST /flags), which re-checks
the conditions; this module only notices and asks. It never blocks the request.
"""
import logging

import boto3
from burn_contracts.events import make_event, utc_now_iso
from burn_contracts.names import CREDENTIALS_TABLE, EVENTS_STREAM, EVENTS_TABLE, REGION

import dispatch

log = logging.getLogger()
NEEDS_REPORT = {"seen", "attempted"}


def maybe_report(row: dict, digest: str) -> str | None:
    """Return the new flag level if this presentation raised it, else None."""
    ref = row["secret_ref"]
    try:
        cred = boto3.resource("dynamodb", region_name=REGION).Table(CREDENTIALS_TABLE).get_item(
            Key={"secret_ref": ref}).get("Item") or {}
        if cred.get("flag_level") not in NEEDS_REPORT:
            return None  # not under review, or already reported: nothing to do
        evidence = {"source": "provider", "fingerprint": digest, "ts": utc_now_iso()}
        out = dispatch.invoke(dispatch.minter_function(), {
            "method": "POST", "path": "/flags",
            "body": {"secret_refs": [ref], "level": "reported", "evidence": evidence}}, wait=True)
        flags = (out or {}).get("body", {}).get("flags", []) if (out or {}).get("statusCode") == 200 else []
        if not any(f["secret_ref"] == ref and f["changed"] for f in flags):
            return None
        event = make_event(
            agent_id="system", source="provider", action="flagged", secret_ref=ref, fingerprint=digest,
            secret_type=row.get("secret_type"), owner="shared", flag_level="reported",
            detail="flagged shared credential presented at the provider; owner should rotate it")
        boto3.resource("dynamodb", region_name=REGION).Table(EVENTS_TABLE).put_item(
            Item={"stream": EVENTS_STREAM, **event})
        return "reported"
    except Exception as exc:  # reporting must never break a legitimate request
        log.error("shared credential report failed for %s: %s", ref, type(exc).__name__)
        return None
