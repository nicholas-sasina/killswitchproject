"""Task progress for replace on burn: one set of rules for the API, the mock and the `burn run` supervisor.

An instance reports item ids only, `{"item": <id>, "state": "started" | "done"}`. Burn stamps each event with its
own receive time and keeps one entry per item, `{"started_at": <ts>, "done_at": <ts or null>}`. When the instance
is burned, only items done before the clean cut-off are trusted; everything else it touched is quarantined.
Times are compared as instants, never as strings: the cut-off can come from evidence other components wrote, in
another ISO 8601 spelling ("Z" or "+00:00", any number of fraction digits).
"""
from datetime import datetime, timezone

from .names import MAX_PROGRESS_EVENTS, MAX_PROGRESS_ITEMS, PROGRESS_STATES, is_item_id


class ProgressError(ValueError):
    """A broken progress rule. The message names the rule; the API answers 400 with it (never 409)."""


def check_event(event) -> tuple[str, str]:
    """(item, state) for one well-formed progress event."""
    if not isinstance(event, dict) or set(event) != {"item", "state"}:
        raise ProgressError("a progress event has exactly item and state")
    if not is_item_id(event["item"]):
        raise ProgressError("item id must match ^[A-Za-z0-9_.:-]{1,64}$")
    if event["state"] not in PROGRESS_STATES:
        raise ProgressError("state must be started or done")
    return event["item"], event["state"]


def check_events(events) -> list[tuple[str, str]]:
    """(item, state) pairs for one heartbeat's progress, in order."""
    if not isinstance(events, list):
        raise ProgressError("progress must be a list")
    if len(events) > MAX_PROGRESS_EVENTS:
        raise ProgressError(f"at most {MAX_PROGRESS_EVENTS} progress events per heartbeat")
    return [check_event(event) for event in events]


def apply(progress: dict | None, events: list[tuple[str, str]], now: str) -> dict:
    """The per-item progress after `events`, each stamped `now` (Burn's clock). The input is not changed.

    `done` for an item never started counts as started and done at once. A repeat never moves a time.
    """
    result = {item: dict(times) for item, times in (progress or {}).items()}
    for item, state in events:
        entry = result.setdefault(item, {"started_at": now, "done_at": None})
        if state == "done" and entry.get("done_at") is None:
            entry["done_at"] = now
    if len(result) > MAX_PROGRESS_ITEMS:
        raise ProgressError(f"at most {MAX_PROGRESS_ITEMS} items per instance")
    return result


def parse_time(value) -> datetime:
    """The UTC instant of an ISO 8601 timestamp with a time zone ("Z" or an offset), fractions optional."""
    if not isinstance(value, str):
        raise ValueError(f"not a timestamp: {value!r}")
    moment = datetime.fromisoformat(value)  # raises ValueError for anything else
    if moment.tzinfo is None:
        raise ValueError(f"timestamp has no time zone: {value!r}")
    return moment.astimezone(timezone.utc)


def counts(progress: dict | None) -> dict:
    """What reads show instead of the map."""
    progress = progress or {}
    return {"items_started": len(progress),
            "items_done": sum(1 for times in progress.values() if times.get("done_at"))}


def classify(progress: dict | None, cutoff: str) -> tuple[list[str], list[str]]:
    """(done, quarantined) item ids for an instance burned with this clean cut-off.

    done: finished before the cut-off. quarantined: everything else it started, in flight at the cut-off or touched
    at or after it, because a possibly compromised agent worked on it. Items never started are in neither list.
    """
    progress, limit = progress or {}, parse_time(cutoff)
    done = sorted(item for item, times in progress.items()
                  if times.get("done_at") and parse_time(times["done_at"]) < limit)  # equal is not before
    trusted = set(done)
    return done, sorted(item for item in progress if item not in trusted)
