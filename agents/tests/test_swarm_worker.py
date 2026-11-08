import asyncio

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
