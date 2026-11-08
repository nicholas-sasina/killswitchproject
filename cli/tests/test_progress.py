"""burn run and task progress: the local endpoint, the queue and its trip on the heartbeat."""
import json
import time
import urllib.error
import urllib.request

import pytest

from burn_contracts.progress import ProgressError
from burn_cli.api import ApiError
from burn_cli.runner.progress import ProgressQueue
from burn_cli.runner.run import EXIT_STOPPED, Instance
from burn_cli.runner.spawn_endpoint import SpawnEndpoint
from test_run import instance, py, stack  # noqa: F401 - stack is a fixture


def ev(item, state="started"):
    return {"item": item, "state": state}


# -- the queue ------------------------------------------------------------------------------------
@pytest.mark.parametrize("event,rule", [
    ({"item": "a b", "state": "started"}, "item id must match"),
    ({"item": "x" * 65, "state": "done"}, "item id must match"),
    ({"item": "a\n", "state": "done"}, "item id must match"),
    ({"item": 7, "state": "done"}, "item id must match"),
    ({"item": "a", "state": "finished"}, "state must be started or done"),
    ({"item": "a", "state": "done", "note": "free text"}, "exactly item and state"),
    (["a", "done"], "exactly item and state"),
])
def test_a_broken_rule_is_refused_with_the_rule_and_nothing_is_queued(event, rule):
    queue = ProgressQueue()
    with pytest.raises(ProgressError, match=rule):
        queue.add(event)
    assert queue.pending() == 0


def test_events_go_out_oldest_first_at_most_50_at_a_time():
    queue = ProgressQueue()
    for i in range(120):
        queue.add(ev(f"item-{i:03d}"))
    sent = []
    while queue.pending():
        batch = queue.batch()
        assert len(batch) <= 50
        sent.append(batch)
        queue.sent(len(batch))
    assert [len(b) for b in sent] == [50, 50, 20]
    assert [e for b in sent for e in b] == [ev(f"item-{i:03d}") for i in range(120)]


def test_a_batch_stays_queued_until_it_is_sent():
    queue = ProgressQueue()
    queue.add(ev("a"))
    assert queue.batch() == queue.batch() == [ev("a")]  # a failed heartbeat sends the same events again
    queue.sent(1)
    assert queue.batch() == [] and queue.pending() == 0


def test_a_repeat_that_changes_nothing_is_not_queued():
    queue = ProgressQueue()
    assert queue.add(ev("a")) is True
    assert queue.add(ev("a")) is False  # a second "started" never moves started_at
    assert queue.add(ev("a", "done")) is True
    assert queue.add(ev("a", "done")) is False
    assert queue.add(ev("a")) is False  # started after done changes nothing either
    queue.sent(len(queue.batch()))
    assert queue.add(ev("a", "done")) is False  # nor does anything the API already has
    assert queue.add(ev("b", "done")) is True  # done without started counts as both
    assert queue.add(ev("b")) is False


def test_more_than_500_items_is_refused_locally():
    queue = ProgressQueue()
    for i in range(500):
        queue.add(ev(f"i{i}", "done"))
    with pytest.raises(ProgressError, match="at most 500 items"):
        queue.add(ev("one-too-many"))
    assert queue.add(ev("i499", "done")) is False  # items it already has are still fine
    assert queue.pending() == 500


def test_dropped_events_can_be_reported_again():
    queue = ProgressQueue()
    queue.add(ev("a"))
    queue.add(ev("b"))
    queue.dropped(1)  # the API refused the batch that held "a"
    assert queue.batch() == [ev("b")]
    assert queue.add(ev("a")) is True


def test_a_queued_event_wakes_the_heartbeat():
    woken = []
    queue = ProgressQueue(on_change=lambda: woken.append(1))
    queue.add(ev("a"))
    queue.add(ev("a"))  # a repeat: nothing new to send
    with pytest.raises(ProgressError):
        queue.add(ev("a b"))
    assert woken == [1]


# -- the local endpoint ---------------------------------------------------------------------------
@pytest.fixture
def endpoint():
    queue = ProgressQueue()
    server = SpawnEndpoint(lambda role, command: {}, "local-token", progress=queue).start()
    server.queue = queue
    yield server
    server.stop()


def post(url, body, token="local-token"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_progress_is_posted_next_to_the_spawn_endpoint(endpoint):
    assert endpoint.progress_url == endpoint.url.removesuffix("/spawn") + "/progress"
    assert post(endpoint.progress_url, ev("item-01")) == (200, {"queued": True})
    assert post(endpoint.progress_url, ev("item-01")) == (200, {"queued": False})
    assert post(endpoint.progress_url, ev("item-01", "done")) == (200, {"queued": True})
    assert endpoint.queue.batch() == [ev("item-01"), ev("item-01", "done")]


@pytest.mark.parametrize("body,rule", [
    ({"item": "../etc", "state": "done"}, "item id must match"),
    ({"item": "a", "state": "nearly"}, "state must be started or done"),
    ({"item": "a", "state": "done", "summary": "free text"}, "exactly item and state"),
    (b"not json", "expected a JSON object"),
])
def test_a_broken_rule_is_a_400_that_names_it(endpoint, body, rule):
    status, reply = post(endpoint.progress_url, body)
    assert status == 400 and rule in reply["error"]
    assert endpoint.queue.pending() == 0


def test_progress_needs_the_local_token(endpoint):
    assert post(endpoint.progress_url, ev("a"), token="")[0] == 401
    assert post(endpoint.progress_url, ev("a"), token="nope")[0] == 401
    assert endpoint.queue.pending() == 0


def test_without_a_queue_there_is_no_progress_endpoint():
    server = SpawnEndpoint(lambda role, command: {}, "local-token").start()
    try:
        assert post(server.url.removesuffix("/spawn") + "/progress", ev("a"))[0] == 404
    finally:
        server.stop()


# -- progress on the heartbeat --------------------------------------------------------------------
class RecordingApi:
    """Heartbeats only; `statuses` are the replies in turn (200 or an error status, 0 for unreachable)."""

    def __init__(self, *statuses):
        self.bodies, self.statuses = [], list(statuses)

    def heartbeat(self, session_id, session_token, progress=None):
        self.bodies.append(progress)
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            raise ApiError(f"POST /instances/{session_id}/heartbeat failed with HTTP {status}", status or None)
        return {}


def supervisor(api):
    inst = Instance(api, "worker", ["true"], gateway_url="http://gateway", host_env={},
                    session={"session_id": "sess_1", "agent_id": "agent-1"})
    inst._token = "session-token-in-memory"
    return inst


def test_each_heartbeat_carries_at_most_50_events_oldest_first():
    api = RecordingApi()
    inst = supervisor(api)
    for i in range(120):
        inst._progress.add(ev(f"item-{i:03d}"))
    assert [inst._beat() for _ in range(4)] == [200] * 4
    assert [len(b) for b in api.bodies[:3]] == [50, 50, 20] and api.bodies[3] == []
    assert [e for b in api.bodies[:3] for e in b] == [ev(f"item-{i:03d}") for i in range(120)]


def test_a_refused_batch_is_logged_and_dropped_and_is_not_a_burn(capsys):
    api = RecordingApi(400)
    inst = supervisor(api)
    inst._progress.add(ev("a"))
    inst._progress.add(ev("b"))
    assert inst._beat() == 400
    assert inst._progress.pending() == 0 and not inst._stopped_by_api
    err = capsys.readouterr().err
    assert "refused 2 progress events" in err and "HTTP 400" in err
    inst._progress.add(ev("c"))
    assert inst._beat() == 200 and api.bodies[-1] == [ev("c")]


@pytest.mark.parametrize("status", [0, 429, 503])
def test_a_transient_failure_keeps_the_batch_for_the_next_heartbeat(status):
    api = RecordingApi(status)
    inst = supervisor(api)
    inst._progress.add(ev("a"))
    assert inst._beat() == status
    assert inst._beat() == 200
    assert api.bodies == [[ev("a")], [ev("a")]]


REPORT = """
    import json, os, sys, time, urllib.request
    def report(item, state):
        req = urllib.request.Request(os.environ["BURN_PROGRESS_URL"], method="POST",
                                     data=json.dumps({"item": item, "state": state}).encode(),
                                     headers={"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"]})
        urllib.request.urlopen(req).read()
"""


def timed(stack):
    """Every request the fake API answers, with when it arrived."""
    seen, original = [], stack.handle

    def handle(method, path, query, body):
        seen.append((time.monotonic(), method, path, body))
        return original(method, path, query, body)

    stack.handle = handle
    return seen


def progress_beats(seen):
    return [(t, body["progress"]) for t, method, path, body in seen
            if path.endswith("/heartbeat") and body.get("progress")]


def test_progress_goes_out_promptly_in_order_and_never_closer_than_the_minimum_gap(stack):
    seen = timed(stack)
    code = REPORT + """
    for i in range(1, 4):
        report(f"item-{i:02d}", "started")
        time.sleep(0.05)
        report(f"item-{i:02d}", "done")
        time.sleep(0.3)
    time.sleep(1.5)"""
    started = time.monotonic()
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.25).run() == 0
    beats = progress_beats(seen)
    assert [e for _, batch in beats for e in batch] == [
        ev(f"item-{i:02d}", state) for i in range(1, 4) for state in ("started", "done")]
    assert beats[0][0] - started < 1.0  # heartbeat_every is a minute: progress itself caused these beats
    assert all(b[0] - a[0] >= 0.2 for a, b in zip(beats, beats[1:]))
    exit_at = next(t for t, _, path, _ in seen if path.endswith("/exit"))
    assert exit_at - beats[-1][0] > 1.0  # all of it went out while the agent ran, not just at its exit


def test_progress_reported_just_before_a_clean_exit_is_sent_before_the_exit(stack):
    seen = timed(stack)
    code = REPORT + """
    report("item-01", "done")"""
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=30.0).run() == 0
    paths = [(path.rsplit("/", 1)[-1], body.get("progress")) for _, _, path, body in seen if "/instances/" in path]
    assert paths[-2:] == [("heartbeat", [ev("item-01", "done")]), ("exit", None)]
    assert next(iter(stack.instances.values()))["status"] == "exited"  # finished: never replaced


def test_a_refused_heartbeat_drops_its_progress_and_the_agent_keeps_running(stack, capfd):
    original = stack.handle

    def refuse_bad(method, path, query, body):
        if path.endswith("/heartbeat") and ev("bad-item") in body.get("progress", []):
            return 400, {"error": "at most 500 items per instance"}
        return original(method, path, query, body)

    stack.handle = refuse_bad
    seen = timed(stack)  # records the refused heartbeat too
    code = REPORT + """
    report("bad-item", "started")
    time.sleep(0.6)
    report("good-item", "started")
    time.sleep(0.6)"""
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1).run() == 0
    assert [batch for _, batch in progress_beats(seen)] == [[ev("bad-item")], [ev("good-item")]]
    assert "refused 1 progress events" in capfd.readouterr().err
    assert next(iter(stack.instances.values()))["status"] == "exited"


def test_a_409_still_means_burned_when_progress_is_waiting(stack):
    stack.heartbeat_status = 409
    code = REPORT + """
    report("item-01", "started")
    time.sleep(30)"""
    started = time.monotonic()
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1, stop_grace=1).run() == EXIT_STOPPED
    assert time.monotonic() - started < 10
