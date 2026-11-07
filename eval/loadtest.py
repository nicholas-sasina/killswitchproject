"""Load test: many simulated agents calling tools through the deployed Gateway.

    uv run python loadtest.py --agents 50 --calls 5 --concurrency 25 [--badges 10]

Each simulated agent opens an MCP connection and makes --calls tools/call requests.
Cognito rate-limits app-client creation, so --badges creates fewer real badges/sessions
and the simulated agents share them (agent i uses badge i % badges). The gateway path
(token check, interceptor, tool Lambda) is identical either way; only the number of
distinct burn-sessions rows differs. The summary says how many were real.
Refuses to run more than --max-calls tool calls so a typo cannot run up a bill.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from live.gateway import Mcp, Throwaway, fetch_token, ssm_value, summarize_ms

OUT = Path(__file__).parent / "out"
MAX_CALLS_DEFAULT = 20_000


async def one_agent(sem, http, url, token, calls, lat, errors):
    async with sem:
        mcp = Mcp(http, url, token)
        try:
            await mcp.start()
            for _ in range(calls):
                s, err, status = await mcp.call_tool("read_ticket", {"ticket_id": "none"})
                lat.append(s)
                if err:
                    errors[status] = errors.get(status, 0) + 1
        except Exception as exc:   # count, do not hide
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1


async def run(agents: int, calls: int, concurrency: int, badge_count: int) -> dict:
    url = ssm_value("/burn/gateway/url")
    with Throwaway(badge_count) as t:
        limits = httpx.Limits(max_connections=concurrency * 2)
        async with httpx.AsyncClient(timeout=60, limits=limits) as http:
            token_start = time.perf_counter()
            token_gate = asyncio.Semaphore(10)

            async def token_for(item):
                async with token_gate:
                    return await fetch_token(http, item["badge"])

            tokens = await asyncio.gather(*(token_for(i) for i in t.items))
            token_seconds = time.perf_counter() - token_start
            lat, errors, sem = [], {}, asyncio.Semaphore(concurrency)
            start = time.perf_counter()
            await asyncio.gather(*(one_agent(sem, http, url, tokens[i % len(tokens)], calls, lat, errors)
                                   for i in range(agents)))
            wall = time.perf_counter() - start
    return {
        "simulated_agents": agents, "calls_per_agent": calls, "concurrency": concurrency,
        "real_badges_and_sessions": badge_count, "token_fetch_seconds_total": round(token_seconds, 1),
        "tool_calls_ok": len(lat) - sum(v for k, v in errors.items() if isinstance(k, int)),
        "tool_calls_attempted": agents * calls, "errors": {str(k): v for k, v in errors.items()},
        "wall_seconds": round(wall, 1), "throughput_calls_per_s": round(len(lat) / wall, 1),
        "latency": summarize_ms(lat) if lat else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=50)
    ap.add_argument("--calls", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=25)
    ap.add_argument("--badges", type=int, default=0, help="real badges to create (default: one per agent)")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS_DEFAULT)
    args = ap.parse_args()
    if args.agents * args.calls > args.max_calls:
        raise SystemExit(f"{args.agents * args.calls} calls exceeds --max-calls {args.max_calls}; raise it deliberately")
    result = asyncio.run(run(args.agents, args.calls, args.concurrency, args.badges or args.agents))
    OUT.mkdir(exist_ok=True)
    (OUT / f"loadtest_{args.agents}x{args.calls}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
