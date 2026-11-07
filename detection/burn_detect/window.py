"""Time- and count-bounded shingle evidence, without retaining payloads."""
from collections import deque
from time import monotonic


class SessionWindow:
    def __init__(self, max_payloads: int = 8, ttl_seconds: float = 30.0, *, clock=monotonic):
        if max_payloads < 1 or ttl_seconds <= 0:
            raise ValueError("window limits must be positive")
        self.max_payloads = max_payloads
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries = deque(maxlen=max_payloads)

    def accumulate(self, evidence: dict) -> dict:
        now = self._clock()
        while self._entries and now - self._entries[0][0] >= self.ttl_seconds:
            self._entries.popleft()
        self._entries.append((now, evidence))
        combined = {}
        for _, payload in self._entries:
            for group, (row, indices, encoding) in payload.items():
                if group not in combined:
                    combined[group] = (row, set(), encoding)
                combined[group][1].update(indices)
        return combined

    def clear(self) -> None:
        self._entries.clear()
