import asyncio
import json

import pytest

from burn_agents.swarm_worker import MAX_CONSECUTIVE_FAILURES, start_offset, work

BADGE_DEAD = "BURN: Mock badge has been revoked"


def run(call, **kw):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    kw.setdefault("tool", "read_inbox")
    kw.setdefault("interval", 3.0)
    return asyncio.run(work(call, sleep=sleep, **kw)), sleeps


def test_calls_the_tool_every_interval_the_requested_number_of_times():
    seen = []

    async def call(tool, args):
        seen.append((tool, args))
        return False, "{}"

    code, sleeps = run(call, calls=3, offset=1.0)
    assert code == 0 and seen == [("read_inbox", {})] * 3
    assert sleeps == [1.0, 3.0, 3.0]  # the offset first, then one interval between calls, none after the last


def test_stops_quietly_when_the_gateway_says_the_badge_is_revoked():
    results = [(False, "{}"), (True, BADGE_DEAD)]

    async def call(tool, args):
        return results.pop(0)

    code, _ = run(call)
    assert code == 0 and results == []


def test_a_refusal_that_is_not_a_dead_badge_does_not_stop_it():
    results = [(True, "BURN: access refused: recipient not allowed"), (False, "{}")]

    async def call(tool, args):
        return results.pop(0)

    assert run(call, calls=2)[0] == 0 and results == []


def test_a_revoked_badge_raised_as_an_exception_also_stops_it():
    async def call(tool, args):
        raise RuntimeError(BADGE_DEAD)

    assert run(call)[0] == 0


def test_gives_up_after_repeated_connection_failures():
    attempts = []

    async def call(tool, args):
        attempts.append(1)
        raise ConnectionError("down")

    assert run(call)[0] == 1 and len(attempts) == MAX_CONSECUTIVE_FAILURES


def test_a_failure_resets_after_a_success():
    results = [ConnectionError()] * (MAX_CONSECUTIVE_FAILURES - 1) + [(False, "{}")] + [ConnectionError()] * 2

    async def call(tool, args):
        out = results.pop(0) if results else (False, "{}")
        if isinstance(out, Exception):
            raise out
        return out

    assert run(call, calls=2)[0] == 0


def test_start_offset_is_deterministic_and_spread_within_one_interval():
    offsets = [start_offset(f"agent-worker-{i:06x}", 3.0) for i in range(50)]
    assert offsets == [start_offset(f"agent-worker-{i:06x}", 3.0) for i in range(50)]
    assert all(0 <= o <= 3.0 for o in offsets) and len(set(offsets)) > 40


def test_root_cause_unwraps_an_exception_group_to_the_first_underlying_error():
    from burn_agents.swarm_worker import root_cause
    inner = ConnectionRefusedError("[Errno 111] Connect call failed ('172.17.0.1', 8701)")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("client failed"), ValueError("later")])
    group.exceptions[0].__cause__ = inner
    assert root_cause(group) == "ConnectionRefusedError: [Errno 111] Connect call failed ('172.17.0.1', 8701)"
    assert root_cause(KeyError("x")).startswith("KeyError")
    assert len(root_cause(RuntimeError("x" * 1000))) < 250


# --- a worker with a task (BURN_TASK), as the launcher gives it for replace on burn ---------------------------------

def run_task(call, task, resume=None, report=None, **kw):
    """Run the task loop with fakes; returns (exit code, the ordered log of progress reports and tool calls, sleeps)."""
    from burn_agents.swarm_worker import work_task
    seen, sleeps = [], []

    async def sleep(seconds):
        sleeps.append(seconds)

    tool = kw.pop("tool", "read_inbox")

    async def attempt(item):  # the scripted worker's attempt: one tool call
        seen.append(("call", tool))
        return await call(tool, {})

    async def default_report(item, state):
        seen.append((state, item))

    kw.setdefault("interval", 3.0)
    code = asyncio.run(work_task(attempt, report or default_report, task=task, resume=resume or {}, sleep=sleep, **kw))
    return code, seen, sleeps


async def ok(tool, args):
    return False, "{}"


def test_each_item_is_started_called_once_and_done_in_order_and_then_it_exits_0():
    code, seen, sleeps = run_task(ok, ["w01-item-01", "w01-item-02"], offset=1.0)
    assert code == 0
    assert seen == [("started", "w01-item-01"), ("call", "read_inbox"), ("done", "w01-item-01"),
                    ("started", "w01-item-02"), ("call", "read_inbox"), ("done", "w01-item-02")]
    assert sleeps == [1.0, 3.0]  # the offset first, then one interval between items, none after the last


def test_a_replacement_skips_the_items_done_and_quarantined_before_it():
    task = [f"w02-item-{i:02d}" for i in range(1, 6)]
    resume = {"done": ["w02-item-01", "w02-item-02"], "quarantined": ["w02-item-03"]}
    code, seen, _ = run_task(ok, task, resume)
    assert code == 0
    assert [e for e in seen if e[0] != "call"] == [("started", "w02-item-04"), ("done", "w02-item-04"),
                                                   ("started", "w02-item-05"), ("done", "w02-item-05")]


def test_a_task_with_nothing_left_makes_no_calls_and_exits_0():
    code, seen, _ = run_task(ok, ["w01-item-01"], {"done": [], "quarantined": ["w01-item-01"]})
    assert code == 0 and seen == []


def test_a_badge_that_dies_mid_task_stops_it_with_the_burned_exit_code(caplog):
    caplog.set_level("INFO", logger="swarm_worker")
    from burn_agents.swarm_worker import EXIT_BURNED

    async def dead(tool, args):
        return True, BADGE_DEAD

    code, seen, _ = run_task(dead, ["w01-item-01", "w01-item-02"])
    assert code == EXIT_BURNED == 3 and seen == [("started", "w01-item-01"), ("call", "read_inbox")]
    assert "burned at w01-item-01, stopping" in caplog.text


def test_a_dead_badge_raised_as_an_exception_mid_task_is_also_burned():
    async def dead(tool, args):
        raise RuntimeError(BADGE_DEAD)

    assert run_task(dead, ["w01-item-01"])[0] == 3


def rejecting(status, on="started"):
    """A burn run progress endpoint that answers `status` to the first `on` report."""
    import httpx

    async def report(item, state):
        if state == on:
            request = httpx.Request("POST", "http://127.0.0.1:9/progress")
            raise httpx.HTTPStatusError(f"{status}", request=request, response=httpx.Response(status, request=request))

    return report


@pytest.mark.parametrize("on", ["started", "done"])
def test_progress_answered_409_means_burned(caplog, on):
    caplog.set_level("INFO", logger="swarm_worker")
    calls = []

    async def call(tool, args):
        calls.append(tool)
        return False, "{}"

    code, _, _ = run_task(call, ["w01-item-01", "w01-item-02"], report=rejecting(409, on))
    assert code == 3 and "burned at w01-item-01, stopping" in caplog.text
    assert calls == ([] if on == "started" else ["read_inbox"])  # no work after the answer


def test_progress_burn_could_not_confirm_stops_it_with_exit_1(caplog):
    calls = []

    async def call(tool, args):
        calls.append(tool)
        return False, "{}"

    code, _, _ = run_task(call, ["w01-item-01"], report=rejecting(503))
    assert code == 1 and calls == [] and "progress not confirmed, stopping" in caplog.text


def test_a_failed_call_is_retried_for_the_same_item_and_reported_done_once():
    results = [ConnectionError("down"), (True, "BURN: access refused: recipient not allowed"), (False, "{}")]

    async def flaky(tool, args):
        out = results.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    code, seen, _ = run_task(flaky, ["w01-item-01"])
    assert code == 0 and results == []
    assert [e for e in seen if e[0] != "call"] == [("started", "w01-item-01"), ("done", "w01-item-01")]


def test_it_gives_up_without_reporting_done_when_the_gateway_stays_unreachable():
    async def down(tool, args):
        raise ConnectionError("down")

    code, seen, _ = run_task(down, ["w01-item-01"])
    assert code == 1 and ("done", "w01-item-01") not in seen
    assert seen.count(("call", "read_inbox")) == MAX_CONSECUTIVE_FAILURES


def test_no_work_is_done_on_an_item_whose_start_the_supervisor_did_not_take():
    calls = []

    async def refuse(item, state):
        raise RuntimeError("400 item id must match")

    async def call(tool, args):
        calls.append(tool)
        return False, "{}"

    code, _, _ = run_task(call, ["w01-item-01"], report=refuse)
    assert code == 1 and calls == []


def test_the_task_and_resume_come_from_the_environment_and_are_checked():
    from burn_agents.swarm_worker import read_task
    assert read_task({}) is None  # no task: today's endless loop
    assert read_task({"BURN_TASK": '["a-1", "a-2"]'}) == (["a-1", "a-2"], {"done": [], "quarantined": []})
    assert read_task({"BURN_TASK": '["a-1"]', "BURN_RESUME": '{"done": ["a-1"], "quarantined": []}'}) == (
        ["a-1"], {"done": ["a-1"], "quarantined": []})
    for bad in ({"BURN_TASK": '"a-1"'}, {"BURN_TASK": '["a 1"]'}, {"BURN_TASK": "not json"},
                {"BURN_TASK": '["a-1"]', "BURN_RESUME": '{"done": "a-1"}'},
                {"BURN_TASK": '["a-1"]', "BURN_RESUME": '{"done": [], "quarantined": [], "note": "x"}'}):
        with pytest.raises(ValueError):
            read_task(bad)


def test_a_dead_badge_that_escapes_the_loop_is_burned_only_for_a_task_worker(monkeypatch):
    from burn_agents import swarm_worker

    async def dead(env, **kw):
        raise RuntimeError(BADGE_DEAD)

    monkeypatch.setattr(swarm_worker, "main_async", dead)
    monkeypatch.delenv("BURN_TASK", raising=False)
    assert swarm_worker.main([]) == 0  # no task: stopping on a dead badge is the normal end, as before
    monkeypatch.setenv("BURN_TASK", '["w01-item-01"]')
    assert swarm_worker.main([]) == 3


def test_with_a_task_it_needs_the_progress_endpoint():
    from burn_agents.swarm_worker import main_async
    code = asyncio.run(main_async({"BURN_MCP_URL": "http://127.0.0.1:1/mcp", "BURN_TASK": '["a-1"]'}))
    assert code == 2


def test_progress_is_posted_to_the_local_endpoint_with_the_local_token_and_a_refusal_raises():
    import httpx
    from burn_agents.swarm_worker import progress_reporter
    sent = []

    def handler(request):
        sent.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(204 if len(sent) == 1 else 400, json={"error": "item id must match"})

    async def go():
        async with progress_reporter("http://127.0.0.1:9/progress", "local-tok",
                                     transport=httpx.MockTransport(handler)) as report:
            await report("w01-item-01", "started")
            with pytest.raises(httpx.HTTPStatusError):
                await report("w01-item-01", "done")

    asyncio.run(go())
    assert sent == [("http://127.0.0.1:9/progress", "Bearer local-tok", {"item": "w01-item-01", "state": "started"}),
                    ("http://127.0.0.1:9/progress", "Bearer local-tok", {"item": "w01-item-01", "state": "done"})]


def test_the_worker_waits_longer_for_a_progress_answer_than_burn_run_waits_for_burn(monkeypatch):
    """burn run holds a "started" until Burn records it, then answers (503 after its wait). The worker must still be
    listening by then, or it gives up first and never learns whether it was burned or Burn was just slow."""
    import inspect
    from pathlib import Path

    import httpx
    from burn_agents import swarm_worker
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "cli"))
    from burn_cli.runner.run import Instance
    from burn_cli.runner.spawn_endpoint import SpawnEndpoint
    waits = [inspect.signature(c.__init__).parameters["progress_wait"].default for c in (Instance, SpawnEndpoint)]
    assert swarm_worker.PROGRESS_TIMEOUT >= max(waits) + 5  # room for the answer after the wait ends

    seen = {}

    class Recording(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Recording)

    async def go():
        async with swarm_worker.progress_reporter("http://127.0.0.1:9/progress", "local-tok"):
            pass

    asyncio.run(go())
    assert seen["timeout"] == swarm_worker.PROGRESS_TIMEOUT
