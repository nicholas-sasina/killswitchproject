"""A small deterministic worker for swarm demos. It makes no model calls, so it costs nothing.

Run under `burn run`: it calls one MCP tool every few seconds through BURN_MCP_URL (the local endpoint that
adds the instance's badge) and stops when the Gateway says its badge is dead.

    burn run --role worker -- python -m burn_agents.swarm_worker
"""
import asyncio
import hashlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from .revoked import is_refusal_text

log = logging.getLogger("swarm_worker")
Call = Callable[[str, dict], Awaitable[tuple[bool, str]]]  # (tool, arguments) -> (is_error, text)
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
    async with mcp_call(url, env.get("BURN_LOCAL_TOKEN", "")) as call:
        return await work(call, tool=env.get("BURN_WORKER_TOOL", "read_inbox"), interval=interval,
                          calls=int(env.get("BURN_WORKER_CALLS", "0")),
                          offset=start_offset(env.get("BURN_AGENT_ID", ""), interval))


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
