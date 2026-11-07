"""Retry with exponential backoff and jitter for calls that can fail transiently."""
import random
import time
from typing import Callable, TypeVar

from .api import ApiError

T = TypeVar("T")


def delay(attempt: int, base: float = 0.5, cap: float = 15.0, rng: Callable[[], float] = random.random) -> float:
    """Full jitter: a random wait up to base * 2**attempt, so parallel callers spread out."""
    return rng() * min(cap, base * 2 ** attempt)


def call(fn: Callable[[], T], *, attempts: int = 8, base: float = 0.5, cap: float = 15.0,
         sleep: Callable[[float], None] = time.sleep, rng: Callable[[], float] = random.random,
         retry_if: Callable[[Exception], bool] = lambda exc: isinstance(exc, ApiError) and exc.retryable) -> T:
    """Run fn, retrying while retry_if says the error is transient. The last error is raised."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retry_if decides what is transient
            if attempt == attempts - 1 or not retry_if(exc):
                raise
            sleep(delay(attempt, base, cap, rng))
    raise AssertionError("unreachable")
