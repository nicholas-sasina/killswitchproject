"""Read-unit measurements supplied after the Gateway fingerprint cache lands."""
import json
import math

from cost import PRICES
from layers import timestamp, source_commit


def load(directory):
    path = directory / "fingerprint_cache.json"
    if not path.exists():
        return {"status": "unmeasured", "reason": "Post-cache read-unit measurements have not been supplied."}
    data = json.loads(path.read_text())
    timestamp(data["measured_at"])
    source_commit(data.get("source_commit"))
    if data.get("cache_enabled") is not True:
        raise ValueError("cache measurements require source_commit and cache_enabled=true")
    rows = []
    for sample in data["samples"]:
        calls, units, size = sample["calls"], sample["read_units"], sample["output_bytes"]
        if type(calls) is not int or calls < 1 or type(size) is not int:
            raise ValueError("sample calls and output_bytes must be integers")
        if type(units) not in (int, float) or not math.isfinite(units) or units < 0:
            raise ValueError("read_units must be finite and nonnegative")
        rows.append({"output_bytes": size, "calls": calls, "read_units_per_call": units/calls,
                     "dynamodb_read_usd_per_1m_calls": round(units/calls * PRICES["dynamodb_per_1m_read_units"][0], 2)})
    if sorted(r["output_bytes"] for r in rows) != [36, 1024, 16384]:
        raise ValueError("supply one measured window for each of 36 B, 1 KiB and 16 KiB")
    return {"status": "measured", "source_commit": data["source_commit"], "measured_at": data["measured_at"],
            "scope": "DynamoDB read cost only, including cache refreshes in each measurement window",
            "measurement_scope": data.get("scope", "owner-supplied (environment unspecified)"),
            "notes": data.get("notes", "Capacity attribution to a specific test has not been independently verified."),
            "samples": sorted(rows, key=lambda r: r["output_bytes"])}
