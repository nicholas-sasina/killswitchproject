"""Task progress the agent reports, waiting to go out with the instance's next heartbeat.

The agent says `{"item": <id>, "state": "started" | "done"}`. Each event is checked with the same rules the API
uses (burn_contracts.progress), so a broken one is refused here, with the rule, instead of costing a heartbeat.
Events leave in the order they came, at most 50 per heartbeat. A repeat that would change nothing on the API (a
second "started", or anything already done) is not queued, so the queue stays small whatever the agent sends.
"""
import threading
from typing import Callable

from burn_contracts import progress as rules
from burn_contracts.names import MAX_PROGRESS_EVENTS

# The API stamps events with its own clock. Here only "started" or "done" matters, so every stamp is the same.
_UNSTAMPED = "local"


class ProgressQueue:
    def __init__(self, on_change: Callable[[], None] = lambda: None):
        self._on_change, self._lock = on_change, threading.Lock()
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

    def sent(self, n: int) -> None:
        """The API took the first n events."""
        with self._lock:
            self._accepted = rules.apply(self._accepted, self._queue[:n], _UNSTAMPED)
            del self._queue[:n]

    def dropped(self, n: int) -> None:
        """The API refused the first n events. They are gone, so the agent may report them again."""
        with self._lock:
            del self._queue[:n]
            self._view = rules.apply(self._accepted, self._queue, _UNSTAMPED)
