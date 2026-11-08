"""Collect account-wide CloudWatch read units for three completed post-cache runs.

This reads AWS metrics; it does not run a load test or deploy the cache. Use only
after the integration owner verifies the declared source commit is deployed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from ddb_reads import measure
from layers import count, source_commit, timestamp


def collect(plan, reader=measure):
    source_commit(plan.get("source_commit"))
    if plan.get("cache_enabled") is not True:
        raise ValueError("the integration owner must confirm cache_enabled=true")
    windows = plan["samples"]
    if not isinstance(windows, list) or sorted(w["output_bytes"] for w in windows) != [36, 1024, 16384]:
        raise ValueError("supply exactly the 36 B, 1 KiB and 16 KiB runs")
    # Validate the whole plan before making any AWS calls.
    intervals = []
    for w in windows:
        start, end = timestamp(w["start"]), timestamp(w["end"])
        if end <= start or any(t.second or t.microsecond for t in (start, end)):
            raise ValueError("windows must have increasing, minute-aligned timestamps")
        if count(w["calls"]) < 1 or not isinstance(w["tool"], str) or not w["tool"]:
            raise ValueError("each window needs actual completed calls and a tool name")
        intervals.append((start, end))
    ordered = sorted(intervals)
    if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("measurement windows must not overlap across output sizes")
    samples = []
    for w in windows:
        observed = reader(w["start"], w["end"], w["calls"], w["tool"], w["output_bytes"])
        reads = {name: table for name, table in observed["tables"].items() if "Read" in table["metric"]}
        if not reads or any(table.get("data_points", 0) < 1 for table in reads.values()):
            raise ValueError("CloudWatch read metrics are missing; wait for publication or investigate before reporting cost")
        samples.append({"output_bytes": w["output_bytes"], "calls": w["calls"],
                        "read_units": sum(table["sum_units"] for table in reads.values()),
                        "window": observed["window"], "tool": w["tool"], "tables": observed["tables"]})
    return {"source_commit": plan["source_commit"], "cache_enabled": True,
            "measured_at": datetime.now(timezone.utc).isoformat(), "scope": "deployed-aws",
            "notes": "Account-wide CloudWatch capacity includes unrelated traffic and cache refreshes. "
                     "The integration owner supplies the declared deployed source commit.",
            "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent/"out/fingerprint_cache.json")
    args = parser.parse_args()
    result = collect(json.loads(args.plan.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(f"Wrote capacity measurements to {args.output}")


if __name__ == "__main__":
    main()
