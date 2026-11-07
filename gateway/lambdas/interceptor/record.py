"""RESPONSE step for tools/call: record every credential the agent is about to see.

Tool output is scanned before the agent gets it. Each credential found is added
to the agent's ledger (burn-exposures) and announced with a "recorded" event.
Burn does not redact: the agent may legitimately need what it read. The point
is to know, at burn time, exactly which credentials this agent has seen.
"""
import logging

import detection
import ledger
import sessions
import shapes
from burn_contracts.events import make_event
from burn_detect import Finding

log = logging.getLogger()


def describe(agent_id: str, finding: Finding, tool: str) -> str:
    if finding.owner == "shared":
        whose = "shared credential"
    elif finding.owner == "agent" and finding.owner_agent_id == agent_id:
        whose = "own credential"
    elif finding.owner == "agent":
        whose = f"credential of {finding.owner_agent_id}"
    else:
        whose = f"unregistered {finding.secret_type} credential"
    return f"{whose} seen in {tool} output"


def record_tool_output(event: dict, session: dict, tool: str, status: int, reply: dict) -> dict:
    agent_id = session["agent_id"]
    text = "\n".join(shapes.result_texts(reply))
    try:
        findings = detection.by_credential(detection.scan(text))
    except ValueError:
        # Too large to check: the agent does not get to see what Burn cannot vouch for.
        sessions.put_event(make_event(
            agent_id=agent_id, source="tool_response", action="refused", session_id=session["session_id"],
            tool=tool, detail=f"{tool} output too large to check; withheld from the agent"))
        return shapes.deny(shapes.request_body(event), "tool output too large to check")

    call = shapes.call_key(event)
    for finding in findings.values():
        if not ledger.record(agent_id, finding, source="tool_response", via=tool, call=call):
            continue  # this call was already recorded (interceptor retry)
        sessions.put_event(make_event(
            agent_id=agent_id, source="tool_response", action="recorded", session_id=session["session_id"],
            tool=tool, secret_ref=finding.secret_ref, fingerprint=finding.fingerprint,
            secret_type=finding.secret_type, owner=finding.owner, owner_agent_id=finding.owner_agent_id,
            detail=describe(agent_id, finding, tool)))
        log.info("recorded agent=%s ref=%s fp=%s...", agent_id, finding.secret_ref, finding.fingerprint[:12])
    return shapes.pass_response(status, reply)
