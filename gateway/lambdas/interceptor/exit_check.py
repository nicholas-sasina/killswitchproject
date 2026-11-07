"""REQUEST step for tools that send data onward: block known credentials.

send_message (to another agent) and notify (to a customer or channel) are
the two tools that move text out of an agent. Their arguments are scanned
before the tool runs, after undoing common encodings (burn_detect). Any
credential Burn minted, current or rotated, stops the call with a "blocked"
event.

A message that passes still carries what the sender knows, in ways a
scanner cannot see (a summary, a hint). So the receiver inherits the
sender's whole ledger: if either agent is burned later, everything both
may have seen gets rotated or flagged.
"""
import detection
import ledger
import sessions
import shapes
from burn_contracts.events import make_event

CALLER_ARG = "burn_caller_agent_id"
# tool -> event source used when it is blocked
CHECKED_TOOLS = {"send_message": "handoff", "notify": "egress"}


def outgoing_text(arguments: dict) -> str:
    """Every string the agent passed, one per line (our own injected id excluded)."""
    return "\n".join(str(v) for k, v in sorted(arguments.items()) if k != CALLER_ARG and v is not None)


def check(event: dict, session: dict, tool: str, arguments: dict) -> dict | None:
    """A deny output if the call must be blocked, else None (and ledger inheritance done)."""
    if tool not in CHECKED_TOOLS:
        return None
    body = shapes.request_body(event)
    try:
        findings = detection.by_credential(detection.scan(outgoing_text(arguments)))
    except ValueError:
        return _block(body, session, tool, arguments, None, "message too large to check")

    known = [f for f in findings.values() if f.secret_ref]  # credentials Burn minted
    if known:
        for finding in known:
            _block(body, session, tool, arguments, finding, f"known credential ({finding.encoding})")
        return shapes.deny(body, "blocked: the message contains a credential")

    if tool == "send_message":
        inherit(session, str(arguments.get("to")), shapes.call_key(event))
    return None


def inherit(session: dict, receiver: str, call: str) -> int:
    """Copy the sender's ledger entries to the receiver. Returns how many were new."""
    sender = session["agent_id"]
    copied = 0
    for entry in ledger.entries(sender):
        if not ledger.copy_entry(entry, receiver, via=sender, call=call):
            continue
        copied += 1
        sessions.put_event(make_event(
            agent_id=receiver, source="handoff", action="inherited", session_id=session["session_id"],
            tool="send_message", secret_ref=entry.get("secret_ref"), fingerprint=entry.get("fingerprint"),
            secret_type=entry.get("secret_type"), owner=entry.get("owner"),
            owner_agent_id=entry.get("owner_agent_id"), target_agent_id=receiver,
            detail=f"inherited from {sender} via send_message"))
    return copied


def _block(body: dict, session: dict, tool: str, arguments: dict, finding, reason: str) -> dict:
    to_agent = str(arguments.get("to")) if tool == "send_message" else None
    channel = str(arguments.get("channel"))[:200] if tool == "notify" else None
    found = {}
    if finding is not None:
        found = {"secret_ref": finding.secret_ref, "fingerprint": finding.fingerprint,
                 "secret_type": finding.secret_type, "owner": finding.owner,
                 "owner_agent_id": finding.owner_agent_id, "encoding": finding.encoding}
    sessions.put_event(make_event(
        agent_id=session["agent_id"], source=CHECKED_TOOLS[tool], action="blocked",
        session_id=session["session_id"], tool=tool, target_agent_id=to_agent, destination=channel,
        detail=f"{tool} blocked: {reason}", **found))
    return shapes.deny(body, f"blocked: {reason}")
