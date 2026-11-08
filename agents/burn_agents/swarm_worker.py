"""A swarm worker: one AI agent (Claude Haiku 4.5 on Bedrock) that works under `burn run`.

    burn run --role worker -- python -m burn_agents.swarm_worker

The model is called with the instance's own AWS key (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, which `burn run`
gives an instance whose role has credentials ["aws_key"]), never with any other credentials, so a burn, which kills
that key, also takes the worker's model away. Its tools are Burn's MCP tools through BURN_MCP_URL (the local
endpoint that adds the instance's badge), as many as its role allows. Every turn is a real model call that costs
money: a turn on one ticket is a few Haiku calls, about one to two cents.

With a task (BURN_TASK, a JSON list of item ids, from the launcher) each item is one model turn on one ticket of the
loaded scenario ("Handle ticket T-1001: ..."). Items map to the tickets list_tickets returns by their place in the
task, modulo the number of tickets, so a replacement handles each item on the same ticket. For each item not already
done or quarantined (BURN_RESUME, when it replaces a burned instance) it tells `burn run` it started it, runs the
turn, tells `burn run` it is done, and exits 0 once every item is handled. A turn that does not finish (throttled,
cut off, an error) is retried and never reported done. Only item ids are reported, so nothing else it writes can
reach a replacement. Without a task it polls read_inbox and gives each message one model turn, until its badge is
refused.

Model calls are capped: at most MAX_MODEL_CALLS in one turn, and at most `--max-model-calls` (default
DEFAULT_WORKER_CALLS) for the whole worker, each call counted before it is made. When the budget is spent the worker
stops (EXIT_CAPPED) without reporting the item it was on as done. Its last line says how many it used ("model calls
used: N of M"), which the launcher reads to keep the whole run under its own cap.

`--scripted` runs a worker with no model instead: one tool call per item, or read_inbox every few seconds without a
task. Only `make swarm MOCK=1` uses it, because the mock servers have no model to offer.

Logs show tool names and status, item ids, ticket ids and token counts; never a tool's output, the model's text, a
key or a token.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable

from burn_contracts.names import PROGRESS_URL_ENV, RESUME_ENV, TASK_ENV, is_item_id

from .revoked import is_refusal_text, looks_revoked

log = logging.getLogger("swarm_worker")
Call = Callable[[str, dict], Awaitable[tuple[bool, str]]]  # (tool, arguments) -> (is_error, text)
Attempt = Callable[[str], Awaitable[tuple[bool, str]]]  # one try at an item -> (is_error, text); raises on failure
Report = Callable[[str, str], Awaitable[None]]  # (item, "started" | "done"); raises if burn run did not take it
MAX_CONSECUTIVE_FAILURES = 5
MAX_RETRY_GAP = 60.0  # seconds: a failed attempt waits interval, 2x, 4x... up to this before the next
# burn run holds a "started" until Burn records it, up to 15 s, then answers 503. Wait longer than that, so the worker
# hears the answer (go ahead, 409 burned, 503 not confirmed) instead of giving up first.
PROGRESS_TIMEOUT = 20.0
EXIT_BURNED = 3  # a task worker stopped because it was burned (as `burn run` exits when it stops a burned agent)
EXIT_CAPPED = 4  # it stopped because its model-call budget was spent

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"  # the only model the stack allows (infra/burn_infra/minter.py)
REGION = "us-east-1"
MAX_MODEL_CALLS = 10  # per turn; a ticket takes three to five
DEFAULT_WORKER_CALLS = 40  # per worker, unless --max-model-calls says otherwise: about ten tickets
USAGE = "model calls used: {used} of {limit}"  # the worker's last line; the launcher reads it (USAGE_RE)
USAGE_RE = re.compile(r"model calls used: (\d+) of (\d+)\s*$")
FINISHED = {"end_turn", "stop_sequence"}  # a turn that ended any other way did not finish its item
# Twenty workers on one account hit Bedrock's limits. botocore retries a throttled call with backoff, and "adaptive"
# also slows this worker's own client down while it is throttled. Strands retries a throttled turn on top of that.
BEDROCK_CLIENT = {"retries": {"mode": "adaptive", "total_max_attempts": 6}, "connect_timeout": 10, "read_timeout": 120}
SYSTEM_PROMPT = """You are a support agent on a SaaS support desk, one of a swarm of identical workers. You get one \
piece of work at a time: a ticket to handle, or a message from another agent.
For a ticket: call read_ticket for it. If it names a config or log file, read it with read_config; if it names a \
customer, look them up with query_customers. Then call notify once with channel "slack:#support" and a two-sentence \
update for the team: the likely cause and the next step.
Reply with one line saying what you did."""


class TurnFailed(Exception):
    """A model turn that ended without finishing (cut off by the model's own limits): its item is not done.

    A turn cut off at a model-call cap raises turn.TurnCut instead; both are retried, and neither reports done.
    """


class CapReached(Exception):
    """The worker's model-call budget is spent (or it is stopping): no more turns."""


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


def retry_gap(interval: float, failures: int) -> float:
    """How long to wait after the `failures`-th failed attempt in a row: doubling from `interval`, capped."""
    return min(interval * 2 ** (failures - 1), MAX_RETRY_GAP)


async def work(call: Call, *, tool: str, interval: float, calls: int = 0, offset: float = 0.0,
               sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    """The scripted worker without a task: call `tool` every `interval` seconds, `calls` times (0 = until stopped).

    Returns an exit code: 0 = stopped because the badge was revoked (or the call budget ran out), 1 = the Gateway is
    unreachable.
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


async def work_task(attempt: Attempt, report: Report, *, task: list[str], resume: dict, interval: float,
                    offset: float = 0.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    """Work through `task`, skipping what `resume` says was done or quarantined. Returns an exit code.

    Per item: report started, `attempt(item)` until one succeeds, report done, then `interval` seconds before the
    next. A failed attempt is retried for the same item, after a growing pause. 0 = every item handled,
    EXIT_BURNED = burned before the end (the Gateway refused the badge, the model refused the instance's key, or
    burn run answered a report with 409), EXIT_CAPPED = the attempt said the model-call budget is spent, 1 = attempts
    kept failing, or burn run did not take a progress report (no more work without it).
    """
    handled = set(resume.get("done", [])) | set(resume.get("quarantined", []))
    todo = [item for item in task if item not in handled]
    log.info("task of %d item(s): %d to do, %d done and %d quarantined before", len(task), len(todo),
             len(resume.get("done", [])), len(resume.get("quarantined", [])))
    await sleep(offset)
    for n, item in enumerate(todo):
        if n:
            await sleep(interval)
        if (code := await _report(report, item, "started")) is not None:
            return code
        failures = 0
        while True:
            try:
                is_error, text = await attempt(item)
            except CapReached as exc:
                log.warning("%s not done: %s, stopping", item, exc)
                return EXIT_CAPPED
            except Exception as exc:  # noqa: BLE001 - a dead connection and a revoked badge look alike here
                if looks_revoked(exc):
                    return _burned(item)
                problem = root_cause(exc)
            else:
                if is_error and is_refusal_text(text):
                    return _burned(item)
                if not is_error:
                    break
                problem = "refused: " + text[:80]
            failures += 1
            log.warning("%s failed (%s): %s", item, failures, problem)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                return 1
            await sleep(retry_gap(interval, failures))
        if (code := await _report(report, item, "done")) is not None:
            return code
        log.info("%s done", item)
    log.info("task finished")
    return 0


def _burned(item: str) -> int:
    log.info("burned at %s, stopping", item)
    return EXIT_BURNED


async def _report(report: Report, item: str, state: str) -> int | None:
    """None once burn run has taken the report, else the exit code to stop with."""
    try:
        await report(item, state)
        return None
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 409:  # burn run heard from Burn that this instance is burned
            return _burned(item)
        if status == 503:  # burn run could not get Burn to confirm it
            log.error("progress not confirmed, stopping at %s %s", item, state)
        else:
            log.error("burn run did not take %s %s (%s): stopping", item, state, root_cause(exc))
        return 1


# --- the AI worker ----------------------------------------------------------------------------------------------------

def ticket_for(item: str, task: list[str], tickets: list[dict]) -> dict:
    """The ticket an item works on: the item's place in the task, modulo the number of tickets.

    It depends only on the task and the loaded scenario, so a replacement, which gets the same task, works each
    item on the same ticket.
    """
    return tickets[task.index(item) % len(tickets)]


@dataclass
class Spend:
    """What the model was used for, from Strands' metrics: logged per turn and once at the end."""
    turns: int = 0
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, result) -> str:
        usage = result.metrics.accumulated_usage
        calls, tokens_in, tokens_out = result.metrics.cycle_count, usage.get("inputTokens", 0), usage.get("outputTokens", 0)
        self.turns += 1
        self.model_calls += calls
        self.input_tokens += tokens_in
        self.output_tokens += tokens_out
        return f"{calls} model call(s), {tokens_in} tokens in, {tokens_out} out"

    def __str__(self) -> str:
        return (f"{self.turns} turn(s), {self.model_calls} model call(s), "
                f"{self.input_tokens} tokens in, {self.output_tokens} out")


def turn_attempt(run_turn: Callable[[str], object], tickets: list[dict], task: list[str], spend: Spend,
                 budget=None) -> Attempt:
    """An `Attempt` that gives an item one model turn on its ticket. A turn that does not finish raises, and so does
    one that `budget` (a turn.CallBudget) has no model call left for."""
    async def attempt(item: str) -> tuple[bool, str]:
        if budget is not None and budget.left < 1:
            raise CapReached(f"model-call budget of {budget.limit} spent")
        ticket = ticket_for(item, task, tickets)
        log.info("%s: handling ticket %s", item, ticket["id"])
        result = await asyncio.to_thread(run_turn, f"Handle ticket {ticket['id']}: {ticket['subject']}")
        used = spend.add(result)
        if result.stop_reason not in FINISHED:
            raise TurnFailed(f"the turn on ticket {ticket['id']} stopped ({result.stop_reason}, {used})")
        log.info("%s: turn finished, %s", item, used)
        return False, ""

    return attempt


def own_aws_session(env: dict):
    """A boto3 session on the instance's own AWS key, or None when `burn run` gave it none.

    Never the default credential chain: a worker without its own key would run its model as whoever started it.
    """
    import boto3
    key_id, secret = env.get("AWS_ACCESS_KEY_ID", ""), env.get("AWS_SECRET_ACCESS_KEY", "")
    if not (key_id and secret):
        return None
    return boto3.Session(aws_access_key_id=key_id, aws_secret_access_key=secret, region_name=REGION)


def wait_for_key(session) -> str:
    """Wait until AWS accepts the instance's fresh key (a few seconds after it is made); return its identity's name.

    Without this the first model call could fail as if the key had been burned.
    """
    from .cloud import retry_while_key_activates
    arn = retry_while_key_activates(lambda: session.client("sts").get_caller_identity()["Arn"])
    return arn.rsplit("/", 1)[-1]


def bedrock_model(session):
    from botocore.config import Config

    from .turn import make_model
    return make_model(MODEL_ID, session, Config(**BEDROCK_CLIENT))


def mcp_client(url: str, local_token: str):
    """Burn's MCP tools through burn run's local endpoint (Strands' client over streamable HTTP)."""
    from strands.tools.mcp import MCPClient
    return MCPClient(url=url, headers={"Authorization": f"Bearer {local_token}"})


async def ai_task(client, model, report: Report, *, task: list[str], resume: dict, interval: float, budget,
                  offset: float = 0.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> int:
    """Work through the task with one model turn per item, on a started MCP client. Returns an exit code."""
    from .gateway import GatewayTools
    from .loop import parse_tickets
    from .revoked import Revoked
    from .turn import TurnRunner
    try:
        tickets = parse_tickets(await asyncio.to_thread(GatewayTools(client).call, "list_tickets"))
    except Revoked:
        log.info("burned before the first item, stopping")
        return EXIT_BURNED
    if not tickets:
        log.error("list_tickets returned no tickets: load a scenario first (see agents/README.md)")
        return 2
    tools = await asyncio.to_thread(client.list_tools_sync)
    log.info("%d ticket(s) in the loaded scenario; tools: %s", len(tickets), ", ".join(t.tool_name for t in tools))
    spend = Spend()
    run_turn = TurnRunner(model, SYSTEM_PROMPT, list(tools), max_model_calls=MAX_MODEL_CALLS, budget=budget)
    try:
        return await work_task(turn_attempt(run_turn, tickets, task, spend, budget), report, task=task, resume=resume,
                               interval=interval, offset=offset, sleep=sleep)
    finally:
        log.info("model use: %s", spend)


def ai_inbox(client, model, *, interval: float, offset: float, budget,
             sleep: Callable[[float], None] = time.sleep) -> int:
    """Without a task: poll read_inbox and give each message one model turn, until the badge is refused or the
    budget is spent."""
    from .gateway import GatewayTools
    from .loop import Worker, run_agent
    from .turn import TurnRunner
    run_turn = TurnRunner(model, SYSTEM_PROMPT, list(client.list_tools_sync()), max_model_calls=MAX_MODEL_CALLS,
                          budget=budget)

    def turn(prompt: str):
        if budget.left < 1:
            raise CapReached(f"model-call budget of {budget.limit} spent")
        return run_turn(prompt)

    def pause(seconds: float) -> None:
        if budget.closed:
            raise CapReached("stopping")
        sleep(seconds)

    pause(offset)
    worker = Worker(GatewayTools(client).call, turn, lambda: None, poll=interval, heartbeat_every=float("inf"),
                    sleep=pause)
    try:
        run_agent(worker.work_inbox)  # returns once the badge is refused; any other error is raised
    except CapReached as exc:
        log.warning("%s, stopping", exc)
        return EXIT_CAPPED
    return 0


async def run_ai(env: dict, *, assigned: tuple[list[str], dict] | None, interval: float, offset: float,
                 max_calls: int) -> int:
    from .turn import CallBudget
    session = own_aws_session(env)
    if session is None:
        print("this instance has no AWS key of its own, so it has no model: give its role credentials [\"aws_key\"]",
              file=sys.stderr)
        return 2
    log.info("model %s with this instance's own AWS key (identity %s)", MODEL_ID,
             await asyncio.to_thread(wait_for_key, session))
    model, local_token, budget = bedrock_model(session), env.get("BURN_LOCAL_TOKEN", ""), CallBudget(max_calls)
    try:
        with mcp_client(env["BURN_MCP_URL"], local_token) as client:
            if not assigned:
                return await asyncio.to_thread(ai_inbox, client, model, interval=interval, offset=offset,
                                               budget=budget)
            async with progress_reporter(env[PROGRESS_URL_ENV], local_token) as report:
                return await ai_task(client, model, report, task=assigned[0], resume=assigned[1], interval=interval,
                                     offset=offset, budget=budget)
    finally:
        budget.close()  # a turn still running in its thread can make no more model calls, so `used` is final
        log.info(USAGE.format(used=budget.used, limit=budget.limit))


# --- plumbing ---------------------------------------------------------------------------------------------------------

@asynccontextmanager
async def progress_reporter(url: str, local_token: str = "", transport=None):
    """A `Report` that posts to burn run's local progress endpoint. Never through a proxy: the endpoint is local."""
    import httpx
    async with httpx.AsyncClient(timeout=PROGRESS_TIMEOUT, trust_env=False, transport=transport) as client:
        async def report(item: str, state: str) -> None:
            reply = await client.post(url, json={"item": item, "state": state},
                                      headers={"Authorization": f"Bearer {local_token}"})
            reply.raise_for_status()

        yield report


@asynccontextmanager
async def mcp_call(url: str, local_token: str = ""):
    """A `Call` backed by the official MCP client over streamable HTTP (the scripted worker's)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {local_token}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool: str, arguments: dict) -> tuple[bool, str]:
                result = await session.call_tool(tool, arguments)
                return bool(result.isError), "\n".join(getattr(c, "text", "") for c in result.content)

            yield call


async def main_async(env: dict, *, scripted: bool = False, interval: float | None = None,
                     max_calls: int = DEFAULT_WORKER_CALLS) -> int:
    url = env.get("BURN_MCP_URL", "")
    if not url:
        print("BURN_MCP_URL is not set: run this under `burn run`", file=sys.stderr)
        return 2
    interval = interval if interval is not None else float(env.get("BURN_WORKER_INTERVAL", "3"))
    try:
        assigned = read_task(env)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if assigned and not env.get(PROGRESS_URL_ENV):
        print(f"{PROGRESS_URL_ENV} is not set: a task needs a `burn run` that takes progress", file=sys.stderr)
        return 2
    offset = start_offset(env.get("BURN_AGENT_ID", ""), interval)
    if not scripted:
        return await run_ai(env, assigned=assigned, interval=interval, offset=offset, max_calls=max_calls)
    tool, local_token = env.get("BURN_WORKER_TOOL", "read_inbox"), env.get("BURN_LOCAL_TOKEN", "")
    if assigned:
        async with mcp_call(url, local_token) as call, progress_reporter(env[PROGRESS_URL_ENV], local_token) as report:
            async def attempt(item: str) -> tuple[bool, str]:
                return await call(tool, {})

            return await work_task(attempt, report, task=assigned[0], resume=assigned[1], interval=interval,
                                   offset=offset)
    async with mcp_call(url, local_token) as call:
        return await work(call, tool=tool, interval=interval, calls=int(env.get("BURN_WORKER_CALLS", "0")),
                          offset=offset)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m burn_agents.swarm_worker", description=__doc__.splitlines()[0])
    parser.add_argument("--scripted", action="store_true",
                        help="no model: one tool call per item, or read_inbox every few seconds (the mock servers)")
    parser.add_argument("--interval", type=float, default=None,
                        help="seconds between items (or inbox polls); default BURN_WORKER_INTERVAL, else 3")
    parser.add_argument("--max-model-calls", type=int, default=DEFAULT_WORKER_CALLS,
                        help=f"hard cap on this worker's model calls (default {DEFAULT_WORKER_CALLS})")
    args = parser.parse_args(argv)
    if args.max_model_calls < 0:
        parser.error("--max-model-calls must be 0 or more")
    return args


def _terminate(signum, frame) -> None:
    """SIGTERM (burn run stopping a burned worker) unwinds like Ctrl-C, so the worker still says what it used."""
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {os.environ.get('BURN_AGENT_ID', 'worker')} %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one request line per call is noise
    logging.getLogger("strands").setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, _terminate)
    try:
        return asyncio.run(main_async(dict(os.environ), scripted=args.scripted, interval=args.interval,
                                      max_calls=args.max_model_calls))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        if looks_revoked(exc):  # a dead badge or key: the normal end without a task, a burn mid-task with one
            log.info("badge revoked/credentials rotated, stopping")
            return EXIT_BURNED if os.environ.get(TASK_ENV) else 0
        log.error("stopping: %s", root_cause(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
