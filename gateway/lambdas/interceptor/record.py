"""RESPONSE step for tools/call: record every credential the agent is about to see,
and (by default) make sure it only ever gets a reference to it.

Tool output is scanned before the agent gets it: every string of the reply, not only its text
parts (structuredContent, embedded resources and JSON-RPC errors too). Placeholders are filled
with real values at read time, so this sees exactly what a real tool would return.

References (the session's `references` mode, from its role; default replace):
- replace: each located credential Burn minted becomes {{burn:ref:<secret_ref>}} (in a
  connection string only the password), so the agent never holds the value and nothing
  it sends, in any encoding, can leak it. The result is scanned again; if a recognized
  credential cannot be located exactly, or a copy is still there, the whole response is
  withheld with a "refused" event.
- report: record what replace would have done, change nothing.
- off: neither.
Canaries are never replaced: they must reach the agent raw for the canary layer to work.

Each credential goes into the agent's ledger (burn-exposures) with status "referenced"
(only the reference reached it) or "seen" (the plaintext did), and source_ref, what the
tool read ('<tool>:<resource>'). The first sighting per (agent, credential) is announced
with a "recorded" event.
"""
import logging

import detection
import ledger
import redact
import sessions
import shapes
from burn_contracts import references, tools
from burn_contracts.events import make_event
from burn_detect import Finding
from burn_detect.normalize import MAX_TEXT

log = logging.getLogger()
WITHHELD = "tool output withheld: it contains a credential Burn could not replace with a reference"


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


def _refuse(event: dict, session: dict, tool: str, detail: str, message: str, finding: Finding | None = None) -> dict:
    found = {}
    if finding is not None:
        found = {"secret_ref": finding.secret_ref, "fingerprint": finding.fingerprint,
                 "secret_type": finding.secret_type, "owner": finding.owner,
                 "owner_agent_id": finding.owner_agent_id}
    sessions.put_event(make_event(
        agent_id=session["agent_id"], source="tool_response", action="refused", session_id=session["session_id"],
        tool=tool, detail=detail[:500], **found))
    return shapes.deny(shapes.request_body(event), message)


def _plan(texts: list[str], per_text: list[list[Finding]], others: list[str]) -> tuple[list[str] | None, str | None]:
    """What replace mode gives the agent: (new texts, None) or (None, why it must be withheld).

    Only text parts are rewritten. Everything the agent would get is then scanned again, so a credential
    anywhere else in the reply, or one that could not be located (split across parts, say), withholds it.
    """
    planned = [redact.apply(text, found) for text, found in zip(texts, per_text)]
    reason = next((why for _, why in planned if why), None)
    if reason:
        return None, reason
    new = [text for text, _ in planned]
    try:
        left = redact.targets(detection.scan("\n".join(new + others)))
    except ValueError:
        return None, "too large to check after replacing"
    if left:
        return None, "a credential was still found after replacing (outside the text parts, or not located exactly)"
    return new, None


def _with_texts(reply: dict, texts: list[str]) -> dict:
    """The reply with its text parts swapped, in order; every other part untouched."""
    remaining = iter(texts)
    content = [{**c, "text": next(remaining)} if isinstance(c, dict) and isinstance(c.get("text"), str) else c
               for c in (reply.get("result") or {}).get("content") or []]
    return {**reply, "result": {**reply["result"], "content": content}}


def record_tool_output(event: dict, session: dict, tool: str, status: int, reply: dict) -> dict:
    agent_id = session["agent_id"]
    texts, others = shapes.result_texts(reply), shapes.other_strings(reply)
    try:
        whole = "\n".join(texts + others)
        if len(whole) > MAX_TEXT:
            raise ValueError("too large")
        per_text = [detection.scan(text) for text in texts]  # where each copy is, to replace it
        everywhere = detection.scan(whole)  # also other parts, and evidence that only adds up across parts
    except ValueError:
        # Too large to check: the agent does not get to see what Burn cannot vouch for.
        return _refuse(event, session, tool, f"{tool} output too large to check; withheld from the agent",
                       "tool output too large to check")

    found = [f for fs in per_text for f in fs] + everywhere
    mode = references.mode(session)
    targets = redact.targets(found)
    replaced, report = set(), None
    if targets and mode in ("replace", "report"):
        new_texts, reason = _plan(texts, per_text, others)
        if mode == "replace":
            if reason:
                return _refuse(event, session, tool,
                               f"{tool} output withheld: {reason} ({len({f.secret_ref for f in targets})} credential(s))",
                               WITHHELD, targets[0])
            if new_texts != texts:
                reply = _with_texts(reply, new_texts)
            replaced = {f.secret_ref for f in targets}
        else:
            report = f"would withhold: {reason}" if reason else "would replace with a reference"
            log.info("report mode agent=%s tool=%s: %s (%d credential(s))", agent_id, tool, report, len(targets))

    arguments = (shapes.request_body(event).get("params") or {}).get("arguments") or {}
    source_ref = tools.source_ref(tool, arguments)
    call = shapes.call_key(event)
    if source_ref != tool and "result" in reply:  # it read a named resource: by-source must find it
        ledger.record_read(agent_id, source_ref, via=tool, call=call)
    target_refs = {f.secret_ref for f in targets}
    for finding in detection.by_credential(found).values():
        referenced = finding.secret_ref in replaced
        is_new = ledger.record(agent_id, finding, source="tool_response", via=tool, call=call,
                               status="referenced" if referenced else "seen", source_ref=source_ref,
                               report=report if finding.secret_ref in target_refs else None)
        if not is_new:
            continue  # already in the ledger: last_seen/seen_in updated, nothing new to announce
        suffix = "; the agent got a reference" if referenced else ""
        if report and finding.secret_ref in target_refs:
            suffix = f"; report mode: {report}"
        sessions.put_event(make_event(
            agent_id=agent_id, source="tool_response", action="recorded", session_id=session["session_id"],
            tool=tool, secret_ref=finding.secret_ref, fingerprint=finding.fingerprint,
            secret_type=finding.secret_type, owner=finding.owner, owner_agent_id=finding.owner_agent_id,
            detail=(describe(agent_id, finding, tool) + suffix)[:500]))
        log.info("recorded agent=%s ref=%s fp=%s... status=%s", agent_id, finding.secret_ref,
                 finding.fingerprint[:12], "referenced" if referenced else "seen")
    return shapes.pass_response(status, reply)
