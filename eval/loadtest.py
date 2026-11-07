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
import random
import time
from pathlib import Path

import httpx

from live.gateway import Mcp, Throwaway, fetch_token, ssm_value, summarize_ms

OUT = Path(__file__).parent / "out"
MAX_CALLS_DEFAULT = 20_000


class Run:
    """Shared state of one load run."""

    def __init__(self):
        self.lat, self.errors = [], {}
        self.calls_log = []          # (agent_index, started_at, refused, is_error)
        self.active = set()          # agents currently between their first and last call
        self.started = 0
        self.revoked_at = None
        self.revoked = set()


async def one_agent(index, sem, http, url, token, calls, think, run):
    async with sem:
        mcp = Mcp(http, url, token)
        run.started += 1
        run.active.add(index)
        try:
            await mcp.start()
            for _ in range(calls):
                started = time.perf_counter()
                s, err, status = await mcp.call_tool("read_ticket", {"ticket_id": "none"})
                run.lat.append(s)
                run.calls_log.append((index, started, mcp.last_refused, err))
                if err and not mcp.last_refused:
                    run.errors[status] = run.errors.get(status, 0) + 1
                await asyncio.sleep(think)   # an agent thinks between tool calls
        except Exception as exc:   # count, do not hide
            run.errors[type(exc).__name__] = run.errors.get(type(exc).__name__, 0) + 1
        finally:
            run.active.discard(index)


async def revoke_midway(run, throwaway, count, agents):
    """When half the agents have started, revoke `count` agents that are still mid-task."""
    while run.started < agents // 2:
        await asyncio.sleep(0.05)
    victims = random.sample(sorted(run.active), min(count, len(run.active)))
    for index in victims:
        throwaway.sessions.update_item(
            Key={"session_id": throwaway.items[index]["session_id"]}, UpdateExpression="SET #s = :r",
            ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":r": "revoked"})
    run.revoked, run.revoked_at = set(victims), time.perf_counter()


def revocation_report(run) -> dict:
    """Calls that began after the revocation finished: revoked agents must all be refused."""
    after = [c for c in run.calls_log if run.revoked_at and c[1] > run.revoked_at]
    gone = [c for c in after if c[0] in run.revoked]
    kept = [c for c in after if c[0] not in run.revoked]
    return {
        "revoked_agents": len(run.revoked),
        "calls_after_revoke_from_revoked": len(gone),
        "refused_of_those": sum(c[2] for c in gone),
        "calls_after_revoke_from_others": len(kept),
        "succeeded_of_those": sum(not c[3] for c in kept),
        "revoked_calls_before_revoke_succeeded": sum(
            not c[3] for c in run.calls_log if c[0] in run.revoked and c[1] <= (run.revoked_at or 0)),
    }


async def run_load(agents: int, calls: int, concurrency: int, badge_count: int, think: float, revoke: int) -> dict:
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
            state, sem = Run(), asyncio.Semaphore(concurrency)
            start = time.perf_counter()
            jobs = [one_agent(i, sem, http, url, tokens[i % len(tokens)], calls, think, state) for i in range(agents)]
            if revoke:
                jobs.append(revoke_midway(state, t, revoke, agents))
            await asyncio.gather(*jobs)
            wall = time.perf_counter() - start
            lat, errors = state.lat, state.errors
    return {
        "simulated_agents": agents, "calls_per_agent": calls, "concurrency": concurrency,
        "real_badges_and_sessions": badge_count, "token_fetch_seconds_total": round(token_seconds, 1),
        "tool_calls_ok": sum(not c[3] for c in state.calls_log),
        "tool_calls_refused": sum(c[2] for c in state.calls_log),
        "tool_calls_attempted": agents * calls, "errors": {str(k): v for k, v in errors.items()},
        "wall_seconds": round(wall, 1), "throughput_calls_per_s": round(len(lat) / wall, 1),
        "latency": summarize_ms([s for s, c in zip(state.lat, state.calls_log) if not c[2]] or lat),
        "revocation": revocation_report(state) if revoke else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=50)
    ap.add_argument("--calls", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=25)
    ap.add_argument("--badges", type=int, default=0, help="real badges to create (default: one per agent)")
    ap.add_argument("--think", type=float, default=0.0, help="seconds an agent waits between calls")
    ap.add_argument("--revoke", type=int, default=0, help="revoke this many sessions mid-load")
    ap.add_argument("--max-calls", type=int, default=MAX_CALLS_DEFAULT)
    args = ap.parse_args()
    if args.agents * args.calls > args.max_calls:
        raise SystemExit(f"{args.agents * args.calls} calls exceeds --max-calls {args.max_calls}; raise it deliberately")
    result = asyncio.run(run_load(args.agents, args.calls, args.concurrency, args.badges or args.agents, args.think, args.revoke))
    OUT.mkdir(exist_ok=True)
    name = f"{args.agents}x{args.calls}"
    (OUT / f"loadtest_{name}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
