"""DynamoDB capacity per tool call: measured from CloudWatch, plus offline candidate counts.

    uv run python ddb_reads.py --start 2026-11-07T21:15:00Z --end 2026-11-07T21:17:00Z \\
        --calls 5000 --tool read_ticket --bytes 36

Sums AWS/DynamoDB Consumed*CapacityUnits (1-minute periods) over a load-test window and
divides by the number of tool calls. The metrics are account-wide, so they include any
other traffic in the window; the load test dominates because it is thousands of calls
in about a minute.

`candidate_counts()` needs no AWS: it counts how many fingerprints the scanner asks the
table for when it scans a sample output of a given size. BatchGetItem charges 0.5 RRU
per requested key (eventually consistent), found or not.
"""
import argparse
import json
import secrets
from pathlib import Path

import boto3
from burn_detect import Scanner

OUT = Path(__file__).parent / "out"
METRICS = {
    "burn-fingerprints": "ConsumedReadCapacityUnits",
    "burn-sessions": "ConsumedReadCapacityUnits",
    "burn-scenario": "ConsumedReadCapacityUnits",
    "burn-events": "ConsumedWriteCapacityUnits",
    "burn-exposures": "ConsumedWriteCapacityUnits",
}


def measure(start: str, end: str, calls: int, tool: str, output_bytes: int) -> dict:
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    per_table = {}
    for table, metric in METRICS.items():
        points = cw.get_metric_statistics(
            Namespace="AWS/DynamoDB", MetricName=metric, Dimensions=[{"Name": "TableName", "Value": table}],
            StartTime=start, EndTime=end, Period=60, Statistics=["Sum"])["Datapoints"]
        total = sum(p["Sum"] for p in points)
        per_table[table] = {"metric": metric, "sum_units": total, "units_per_call": round(total / calls, 3)}
    return {"window": [start, end], "calls": calls, "tool": tool, "output_bytes": output_bytes,
            "tables": per_table,
            "read_units_per_call": round(sum(v["units_per_call"] for v in per_table.values() if "Read" in v["metric"]), 2),
            "write_units_per_call": round(sum(v["units_per_call"] for v in per_table.values() if "Write" in v["metric"]), 3)}


def config_text(size: int) -> str:
    """A config-file-like output: many different keys, hosts and numbers (not repetitive prose)."""
    lines, i = [], 0
    while sum(len(x) + 1 for x in lines) < size:
        i += 1
        lines.append(f"service_{i}.endpoint: https://svc{i}-{secrets.token_hex(3)}.internal.example/v{secrets.randbelow(9)}")
        lines.append(f"service_{i}.timeout_ms: {secrets.randbelow(60000)}  # owner team-{secrets.token_hex(2)}")
    return "\n".join(lines)[:size]


def candidate_counts(sizes=(1024, 4096, 16384)) -> dict:
    """Fingerprints requested per scan of an output of each size (a config-file-like text, and random hex as a worst case)."""
    counts = {}
    for size in sizes:
        for kind, text in (("config_file", config_text(size)),
                           ("random_hex", secrets.token_hex(size // 2 + 1)[:size])):
            asked = []
            Scanner(secrets.token_bytes(32), lambda fps: asked.append(len(fps)) or {}).scan(text)
            counts[f"{kind}_{size}"] = {"bytes": size, "kind": kind, "fingerprints_requested": sum(asked),
                                        "read_units": sum(asked) * 0.5}
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--calls", type=int, required=True)
    ap.add_argument("--tool", required=True)
    ap.add_argument("--bytes", type=int, required=True)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    result = {"measured": measure(a.start, a.end, a.calls, a.tool, a.bytes), "candidates": candidate_counts()}
    (OUT / "ddb_reads.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
