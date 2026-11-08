"""A small deterministic worker for swarm demos. It makes no model calls, so it costs nothing.

Run under `burn run`: it calls one MCP tool every few seconds through BURN_MCP_URL (the local endpoint that
adds the instance's badge) and stops when the Gateway says its badge is dead.

    burn run --role worker -- python -m burn_agents.swarm_worker

With a task (BURN_TASK, a JSON list of item ids, from the launcher) it works through the items instead: for each one
not already done or quarantined (BURN_RESUME, when it replaces a burned instance) it tells `burn run` it started it,
makes one tool call, tells `burn run` it is done, and exits 0 once every item is handled. Only item ids are
reported, so nothing else it writes can reach a replacement.
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from burn_contracts.names import PROGRESS_URL_ENV, RESUME_ENV, TASK_ENV, is_item_id

from .revoked import is_refusal_text

log = logging.getLogger("swarm_worker")
Call = Callable[[str, dict], Awaitable[tuple[bool, str]]]  # (tool, arguments) -> (is_error, text)
Report = Callable[[str, str], Awaitable[None]]  # (item, "started" | "done"); raises if burn run did not take it
MAX_CONSECUTIVE_FAILURES = 5


def root_cause(exc: BaseException) -> str:
    """The first underlying error, so an ExceptionGroup from the MCP client's task group explains itself.

    Class and message only; the message of a connection error names a URL or status, never a credential.
    """
    seen = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        children = getattr(exc, "exceptions", None)
        nxt = children[0] if children else exc.__cause__ or exc.__context__
        if nxt is None:
            break
        exc = nxt
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def start_offset(agent_id: str, interval: float) -> float:
    """Spread workers over one interval without randomness: the same agent id always starts at the same point."""
    return int(hashlib.sha256(agent_id.encode()).hexdigest()[:4], 16) / 0xFFFF * interval


async def work(call: Call, *, tool: str, interval: float, calls: int = 0, offset: float = 0.0,
               sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    """Call `tool` every `interval` seconds, `calls` times (0 = until stopped). Returns an exit code.

    0 = stopped because the badge was revoked (or the call budget ran out), 1 = the Gateway is unreachable.
    """
    await sleep(offset)
    made = failures = 0
    while not calls or made < calls:
        try:
            is_error, text = await call(tool, {})
        except Exception as exc:  # noqa: BLE001 - a dead connection and a revoked badge look alike here
            if is_refusal_text(str(exc)):
                log.info("badge revoked, stopping")
                return 0
            failures += 1
            log.warning("gateway call failed (%s): %s", failures, root_cause(exc))
            if failures >= MAX_CONSECUTIVE_FAILURES:
                return 1
        else:
            failures = 0
            made += 1
            if is_error and is_refusal_text(text):
                log.info("badge revoked, stopping")
                return 0
            log.info("%s #%d %s", tool, made, "refused: " + text[:80] if is_error else "ok")
        if calls and made >= calls:
            break
        await sleep(interval)
    return 0


def read_task(env: dict) -> tuple[list[str], dict] | None:
    """(task, resume) from BURN_TASK and BURN_RESUME, or None without a task. Anything but item ids is a ValueError."""
    if not env.get(TASK_ENV):
        return None
    try:
        task = json.loads(env[TASK_ENV])
        resume = json.loads(env.get(RESUME_ENV) or '{"done": [], "quarantined": []}')
    except json.JSONDecodeError as exc:
        raise ValueError(f"{TASK_ENV} and {RESUME_ENV} must be JSON ({exc.msg})") from None
    if not isinstance(task, list) or not all(is_item_id(item) for item in task):
        raise ValueError(f"{TASK_ENV} must be a list of item ids")
    if (not isinstance(resume, dict) or set(resume) != {"done", "quarantined"}
            or not all(isinstance(ids, list) and all(is_item_id(i) for i in ids) for ids in resume.values())):
        raise ValueError(f"{RESUME_ENV} must be {{\"done\": [ids], \"quarantined\": [ids]}}")
    return task, resume


async def work_task(call: Call, report: Report, *, task: list[str], resume: dict, tool: str, interval: float,
                    offset: float = 0.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    """Work through `task`, skipping what `resume` says was done or quarantined. Returns an exit code.

    Per item: report started, one `tool` call, report done, then `interval` seconds before the next. A failed call is
    retried for the same item. 0 = every item handled (or the badge was revoked: Burn decides what happens to the
    rest), 1 = the Gateway stayed unreachable, or burn run did not take a progress report (no more work without it).
    """
    handled = set(resume.get("done", [])) | set(resume.get("quarantined", []))
    todo = [item for item in task if item not in handled]
    log.info("task of %d item(s): %d to do, %d done and %d quarantined before", len(task), len(todo),
             len(resume.get("done", [])), len(resume.get("quarantined", [])))
    await sleep(offset)
    for n, item in enumerate(todo):
        if n:
            await sleep(interval)
        if not await _report(report, item, "started"):
            return 1
        failures = 0
        while True:
            try:
                is_error, text = await call(tool, {})
            except Exception as exc:  # noqa: BLE001 - a dead connection and a revoked badge look alike here
                if is_refusal_text(str(exc)):
                    log.info("badge revoked, stopping at %s", item)
                    return 0
                problem = root_cause(exc)
            else:
                if is_error and is_refusal_text(text):
                    log.info("badge revoked, stopping at %s", item)
                    return 0
                if not is_error:
                    break
                problem = "refused: " + text[:80]
            failures += 1
            log.warning("%s for %s failed (%s): %s", tool, item, failures, problem)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                return 1
            await sleep(interval)
        if not await _report(report, item, "done"):
            return 1
        log.info("%s done", item)
    log.info("task finished")
    return 0


async def _report(report: Report, item: str, state: str) -> bool:
    try:
        await report(item, state)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("burn run did not take %s %s (%s): stopping", item, state, root_cause(exc))
        return False


@asynccontextmanager
async def progress_reporter(url: str, local_token: str = "", transport=None):
    """A `Report` that posts to burn run's local progress endpoint. Never through a proxy: the endpoint is local."""
    import httpx
    async with httpx.AsyncClient(timeout=10, trust_env=False, transport=transport) as client:
        async def report(item: str, state: str) -> None:
            reply = await client.post(url, json={"item": item, "state": state},
                                      headers={"Authorization": f"Bearer {local_token}"})
            reply.raise_for_status()

        yield report


@asynccontextmanager
async def mcp_call(url: str, local_token: str = ""):
    """A `Call` backed by the official MCP client over streamable HTTP."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {local_token}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool: str, arguments: dict) -> tuple[bool, str]:
                result = await session.call_tool(tool, arguments)
                return bool(result.isError), "\n".join(getattr(c, "text", "") for c in result.content)

            yield call


async def main_async(env: dict) -> int:
    url = env.get("BURN_MCP_URL", "")
    if not url:
        print("BURN_MCP_URL is not set: run this under `burn run`", file=sys.stderr)
        return 2
    interval = float(env.get("BURN_WORKER_INTERVAL", "3"))
    try:
        assigned = read_task(env)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    tool, offset = env.get("BURN_WORKER_TOOL", "read_inbox"), start_offset(env.get("BURN_AGENT_ID", ""), interval)
    if assigned:
        if not env.get(PROGRESS_URL_ENV):
            print(f"{PROGRESS_URL_ENV} is not set: a task needs a `burn run` that takes progress", file=sys.stderr)
            return 2
        local_token = env.get("BURN_LOCAL_TOKEN", "")
        async with mcp_call(url, local_token) as call, progress_reporter(env[PROGRESS_URL_ENV], local_token) as report:
            return await work_task(call, report, task=assigned[0], resume=assigned[1], tool=tool, interval=interval,
                                   offset=offset)
    async with mcp_call(url, env.get("BURN_LOCAL_TOKEN", "")) as call:
        return await work(call, tool=tool, interval=interval, calls=int(env.get("BURN_WORKER_CALLS", "0")),
                          offset=offset)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {os.environ.get('BURN_AGENT_ID', 'worker')} %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one request line per call is noise
    try:
        return asyncio.run(main_async(dict(os.environ)))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        if is_refusal_text(str(exc)):
            return 0
        log.error("stopping: %s", root_cause(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
