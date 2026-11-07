"""Per-tool-call latency through the deployed Gateway, plus the interceptor's own share.

    uv run python latency_gateway.py [--calls 200]

One throwaway agent makes sequential calls. The interceptor and tool Lambda durations come
from their CloudWatch REPORT lines for the same time window.
"""
import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import boto3
import httpx

from live.gateway import Mcp, REGION, Throwaway, fetch_token, ssm_value, summarize_ms

OUT = Path(__file__).parent / "out"
REPORT = re.compile(r"Duration: ([\d.]+) ms\s+Billed Duration: (\d+) ms\s+Memory Size: (\d+) MB")


def lambda_reports(function: str, start_ms: int, end_ms: int) -> list[tuple[float, int, int]]:
    logs = boto3.client("logs", region_name=REGION)
    found, token = [], None
    while True:
        kwargs = dict(logGroupName=f"/aws/lambda/{function}", startTime=start_ms, endTime=end_ms,
                      filterPattern='"REPORT RequestId"')
        if token:
            kwargs["nextToken"] = token
        page = logs.filter_log_events(**kwargs)
        for event in page["events"]:
            m = REPORT.search(event["message"])
            if m:
                found.append((float(m[1]), int(m[2]), int(m[3])))
        token = page.get("nextToken")
        if not token:
            return found


async def measure(calls: int, warmup: int = 10) -> dict:
    url = ssm_value("/burn/gateway/url")
    with Throwaway(1) as t:
        item = t.items[0]
        async with httpx.AsyncClient(timeout=30) as http:
            mcp = Mcp(http, url, await fetch_token(http, item["badge"]))
            await mcp.start()
            for _ in range(warmup):
                await mcp.call_tool("read_ticket", {"ticket_id": "none"})
            window_start = int(time.time() * 1000)
            seconds, errors = [], 0
            for _ in range(calls):
                s, err, status = await mcp.call_tool("read_ticket", {"ticket_id": "none"})
                seconds.append(s)
                errors += status != 200
        window_end = int(time.time() * 1000)
    await asyncio.sleep(25)   # CloudWatch ingestion lag
    reports = {fn: lambda_reports(fn, window_start, window_end + 30000)
               for fn in ("burn-gateway-interceptor", "burn-gateway-tools")}
    result = {"calls": calls, "http_errors": errors, "client_round_trip": summarize_ms(seconds)}
    for fn, rows in reports.items():
        if rows:
            result[fn] = {**summarize_ms([r[0] / 1000 for r in rows]), "invocations": len(rows),
                          "per_call": round(len(rows) / calls, 2), "memory_mb": rows[0][2],
                          "mean_billed_ms": round(sum(r[1] for r in rows) / len(rows), 1)}
    if "burn-gateway-interceptor" in result:
        i = result["burn-gateway-interceptor"]
        # Two interceptor invocations per tools/call (request + response). Medians, not means or
        # counts: the log group is shared, so other traffic in the window would skew those.
        result["interceptor_share_of_median"] = round(2 * i["p50_ms"] / result["client_round_trip"]["p50_ms"], 3)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=200)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    result = asyncio.run(measure(args.calls))
    (OUT / "latency_gateway.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
