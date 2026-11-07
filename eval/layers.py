"""Aggregate private owner-produced observations; absent evidence stays unmeasured.

Input files live in gitignored out/. Reports contain counts and timings only.
"""
from datetime import datetime
import json
import math
import re
from pathlib import Path


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamps must include a timezone")
    return parsed


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("evidence counts must be nonnegative integers")
    return value


def source_commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{7,40}", value):
        raise ValueError("source_commit must be a Git commit hash")
    return value


def measure(kind, evidence):
    source_commit(evidence.get("source_commit"))
    timestamp(evidence["measured_at"])
    records = evidence["records"]
    if not isinstance(records, list) or not records:
        raise ValueError("evidence must contain observations")
    ids = [r["event_id"] for r in records]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("observation event ids must be nonempty and unique")
    out = {"status": "measured", "source_commit": evidence["source_commit"],
           "measured_at": evidence["measured_at"], "observations": len(records)}
    if kind == "references":
        recognized = exposed = 0
        for r in records:
            n, leaked = count(r["recognized"]), count(r["in_context"])
            if leaked > n:
                raise ValueError("in_context cannot exceed recognized")
            recognized += n
            exposed += leaked
        out.update(recognized=recognized, in_context=exposed,
                   exposure_rate=exposed / recognized if recognized else None,
                   target_met=exposed == 0 if recognized else None)
    elif kind == "canaries":
        detected, latencies = 0, []
        for r in records:
            used = timestamp(r["used_at"])
            seen = timestamp(r["detected_at"]) if r.get("detected_at") else None
            burned = timestamp(r["burned_at"]) if r.get("burned_at") else None
            if seen and seen < used or burned and (not seen or burned < seen):
                raise ValueError("canary timestamps are out of order")
            detected += seen is not None
            if burned:
                seconds = (burned - used).total_seconds()
                if not math.isfinite(seconds):
                    raise ValueError("invalid burn latency")
                latencies.append(seconds)
        ordered = sorted(latencies)
        out.update(uses=len(records), detected=detected, detection_rate=detected / len(records),
                   burned=len(latencies), target_met=detected == len(records),
                   burn_seconds_p50=ordered[len(ordered)//2] if ordered else None,
                   burn_seconds_p95=ordered[min(len(ordered)-1, int(len(ordered)*.95))] if ordered else None)
    else:
        raise ValueError("unknown measurement layer")
    return out


def load(directory: Path):
    result = {}
    for kind in ("references", "canaries"):
        path = directory / f"{kind}.json"
        result[kind] = measure(kind, json.loads(path.read_text())) if path.exists() else {
            "status": "unmeasured", "reason": "Owner-produced observations have not been supplied."}
    return result
