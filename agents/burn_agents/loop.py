"""The work loop. Pure logic with injected dependencies, so tests use fakes.

Ticket agents walk the queue; every other agent polls its inbox. Any sign
that the agent was burned raises Revoked/TokenRefused/auth errors, and
run_agent turns that into one clear log line and a clean return.
"""
import json
import logging
import time

from .revoked import looks_revoked

log = logging.getLogger("burn_agents")


def parse_tickets(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("tickets", [])
    return [{"id": str(t["id"]), "subject": t.get("subject", "")} for t in data]


def parse_messages(text: str) -> list[dict]:
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return [{"from": "unknown", "text": text}]  # plain text inbox
    if isinstance(data, dict):
        data = data.get("messages", [])
    return [{"from": m.get("from", "unknown"), "text": m.get("text", "")} for m in data]


class Worker:
    def __init__(self, call_tool, run_turn, heartbeat, *, pause=20.0, poll=5.0,
                 heartbeat_every=300.0, repeat=True, gate=lambda: None,
                 sleep=time.sleep, clock=time.monotonic):
        self.call_tool, self.run_turn, self.heartbeat = call_tool, run_turn, heartbeat
        self.pause, self.poll, self.repeat, self.gate = pause, poll, repeat, gate
        self.sleep, self.clock, self.heartbeat_every = sleep, clock, heartbeat_every
        self._last_beat = clock()

    def _maybe_heartbeat(self) -> None:
        if self.clock() - self._last_beat >= self.heartbeat_every:
            self._last_beat = self.clock()
            self.heartbeat()

    def work_tickets(self) -> None:
        while True:
            for ticket in parse_tickets(self.call_tool("list_tickets", {})):
                self.gate()
                log.info("working ticket %s", ticket["id"])
                self.run_turn(f"Handle ticket {ticket['id']}: {ticket['subject']}")
                self._maybe_heartbeat()
                self.sleep(self.pause)
            if not self.repeat:
                return
            log.info("queue finished, checking again later")
            self.sleep(self.pause * 3)

    def work_inbox(self, max_polls: int | None = None) -> None:
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            for msg in parse_messages(self.call_tool("read_inbox", {})):
                log.info("handling a message from %s", msg["from"])
                self.run_turn(f"Message from {msg['from']}:\n{msg['text']}")
            self._maybe_heartbeat()
            self.sleep(self.poll)


def run_agent(work) -> str:
    """Run `work()`; return "revoked" or "done". Unexpected errors still raise."""
    try:
        work()
        return "done"
    except Exception as exc:  # noqa: BLE001 - we re-raise anything that is not a burn
        if looks_revoked(exc):
            log.error("badge revoked/credentials rotated - stopping")
            return "revoked"
        raise
