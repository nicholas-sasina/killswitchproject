"""Task progress the agent reports, waiting to go out with the instance's next heartbeat.

The agent says `{"item": <id>, "state": "started" | "done"}`. Each event is checked with the same rules the API
uses (burn_contracts.progress), so a broken one is refused here, with the rule, instead of costing a heartbeat.
Events leave in the order they came, at most 50 per heartbeat. A repeat that would change nothing on the API (a
second "started", or anything already done) is not queued, so the queue stays small whatever the agent sends.

An agent must not act on an item before Burn has recorded it as started: if the instance were burned in between,
Burn would not know the item was touched, and a replacement would do it again. So a start can be waited on until
a heartbeat carrying it was accepted (`wait_recorded`).
"""
import threading
import time
from typing import Callable

from burn_contracts import progress as rules
from burn_contracts.names import MAX_PROGRESS_EVENTS

# The API stamps events with its own clock. Here only "started" or "done" matters, so every stamp is the same.
_UNSTAMPED = "local"


class ProgressQueue:
    def __init__(self, on_change: Callable[[], None] = lambda: None):
        self._on_change, self._lock = on_change, threading.Lock()
        self._changed = threading.Condition(self._lock)  # notified when the API took, refused or ended something
        self._gone, self._refusal, self._waiting = False, "", 0
        self._queue: list[tuple[str, str]] = []
        self._accepted: dict = {}  # what the API took, built with the shared rules
        self._view: dict = {}  # _accepted plus the queue: what the API will hold once the queue is sent

    def add(self, event) -> bool:
        """Queue one event; False for a repeat that changes nothing. Raises ProgressError naming a broken rule."""
        item, state = rules.check_event(event)
        with self._lock:
            view = rules.apply(self._view, [(item, state)], _UNSTAMPED)  # refuses the 501st item
            if view == self._view:
                return False
            self._view = view
            self._queue.append((item, state))
        self._on_change()
        return True

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def batch(self) -> list[dict]:
        """The oldest events, at most 50: the progress for the next heartbeat. They stay queued until sent()."""
        with self._lock:
            return [{"item": item, "state": state} for item, state in self._queue[:MAX_PROGRESS_EVENTS]]

    def waiting(self) -> bool:
        """True while the agent waits for a start to be recorded, so a failed heartbeat is worth retrying soon."""
        with self._lock:
            return self._waiting > 0

    def confirmed_by(self, n: int, reply) -> bool:
        """True when a heartbeat reply's progress counts show the API holds the first n events.

        An API that took the heartbeat but ignored its progress (one from before progress existed) must not
        count as having recorded it, or a waiting start would be told to go ahead on a start Burn never saw.
        """
        with self._lock:
            expected = rules.counts(rules.apply(self._accepted, self._queue[:n], _UNSTAMPED))
        return isinstance(reply, dict) and all(
            isinstance(reply.get(key), int) and reply[key] >= count for key, count in expected.items())

    def sent(self, n: int) -> None:
        """The API took the first n events."""
        with self._lock:
            self._accepted = rules.apply(self._accepted, self._queue[:n], _UNSTAMPED)
            del self._queue[:n]
            self._changed.notify_all()

    def dropped(self, n: int, reason: str = "") -> None:
        """The API refused the first n events. They are gone, so the agent may report them again."""
        with self._lock:
            del self._queue[:n]
            self._view = rules.apply(self._accepted, self._queue, _UNSTAMPED)
            self._refusal = reason
            self._changed.notify_all()

    def gone(self) -> None:
        """The instance was burned or has exited: nothing more will be recorded."""
        with self._lock:
            self._gone = True
            self._changed.notify_all()

    def wait_recorded(self, item: str, timeout: float) -> tuple[str, str]:
        """Wait until the API has recorded `item` as started. Returns (outcome, detail), the outcome one of
        "recorded", "gone" (burned or exited), "refused" (a 400 dropped it; detail says why) or "timeout"."""
        deadline, told = time.monotonic() + timeout, False
        with self._changed:
            self._waiting += 1
            try:
                while True:
                    if item in self._accepted:
                        return "recorded", ""
                    if self._gone:
                        return "gone", ""
                    if item not in self._view:
                        return "refused", self._refusal
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return "timeout", ""
                    if not told:  # a start now waits: the heartbeat may go after the shorter gap
                        told = True
                        self._on_change()
                    self._changed.wait(remaining)
            finally:
                self._waiting -= 1
