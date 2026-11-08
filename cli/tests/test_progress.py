"""burn run and task progress: the local endpoint, the queue and its trip on the heartbeat."""
import json
import re
import threading
import time
import urllib.error
import urllib.request

import pytest

from burn_contracts.progress import ProgressError
import burn_cli.runner.run as runmod
from burn_cli.api import ApiError
from burn_cli.main import run as cli
from burn_cli.runner.env import BadTask, describe, task_values
from burn_cli.runner.progress import ProgressQueue
from burn_cli.runner.run import EXIT_STOPPED, Instance
from burn_cli.runner.spawn_endpoint import SpawnEndpoint
from fake_api import API_TOKEN
from test_run import DUMP_ENV, REAL_POPEN, instance, py, stack  # noqa: F401 - stack is a fixture


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
    server = SpawnEndpoint(lambda role, command: {}, "local-token", progress=queue, progress_wait=10.0).start()
    server.queue = queue
    yield server
    server.stop()


def in_background(call):
    """Run a blocking call on a thread; its result lands in the returned dict."""
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("reply", call()), daemon=True)
    thread.start()
    return thread, out


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
    assert post(endpoint.progress_url, ev("item-01", "done")) == (200, {"queued": True})
    assert post(endpoint.progress_url, ev("item-01", "done")) == (200, {"queued": False})
    assert endpoint.queue.batch() == [ev("item-01", "done")]


def test_done_is_answered_at_once_and_sent_later(endpoint):
    started = time.monotonic()
    assert post(endpoint.progress_url, ev("item-01", "done")) == (200, {"queued": True})
    assert time.monotonic() - started < 5 and endpoint.queue.pending() == 1  # well within the 10 s a start may wait


def test_started_is_answered_only_once_burn_has_recorded_it(endpoint):
    thread, out = in_background(lambda: post(endpoint.progress_url, ev("item-01")))
    thread.join(0.4)
    assert thread.is_alive() and endpoint.queue.batch() == [ev("item-01")]  # queued, not yet recorded
    endpoint.queue.sent(1)  # a heartbeat carrying it got a 200
    thread.join(2)
    assert out["reply"] == (200, {"queued": True, "recorded": True})
    assert post(endpoint.progress_url, ev("item-01")) == (200, {"queued": False, "recorded": True})  # already


def test_started_on_a_burned_instance_is_a_409_so_the_agent_stops_before_acting(endpoint):
    thread, out = in_background(lambda: post(endpoint.progress_url, ev("item-01")))
    thread.join(0.2)
    endpoint.queue.gone()  # the heartbeat got a 409
    thread.join(2)
    assert out["reply"][0] == 409 and "burned" in out["reply"][1]["error"]
    assert post(endpoint.progress_url, ev("item-02"))[0] == 409  # and every later start, at once


def test_a_start_burn_refused_is_a_400_with_its_reason(endpoint):
    thread, out = in_background(lambda: post(endpoint.progress_url, ev("item-01")))
    thread.join(0.2)
    endpoint.queue.dropped(1, "HTTP 400: at most 500 items per instance")
    thread.join(2)
    assert out["reply"][0] == 400 and "at most 500 items per instance" in out["reply"][1]["error"]


def test_a_start_burn_has_not_recorded_in_time_is_a_503_and_stays_queued():
    queue = ProgressQueue()
    server = SpawnEndpoint(lambda role, command: {}, "local-token", progress=queue, progress_wait=0.3).start()
    try:
        status, reply = post(server.progress_url, ev("item-01"))
        assert status == 503 and "not recorded" in reply["error"]
        assert queue.batch() == [ev("item-01")]
    finally:
        server.stop()


def test_a_waiting_start_blocks_neither_spawn_nor_other_reports(endpoint):
    thread, out = in_background(lambda: post(endpoint.progress_url, ev("item-01")))
    thread.join(0.1)
    started = time.monotonic()
    assert post(endpoint.url, {"role": "worker", "command": ["true"]})[0] == 200
    assert post(endpoint.progress_url, ev("item-02", "done"))[0] == 200
    assert time.monotonic() - started < 5 and thread.is_alive()
    endpoint.queue.sent(2)
    thread.join(2)
    assert out["reply"][0] == 200


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
                    session={"session_id": "sess_1", "agent_id": "agent-1"}, sleep=lambda _: None)
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
    assert inst._beat() == 200 and api.bodies[-1] == []  # the API recorded nothing, liveness included: a bare beat
    assert inst._beat() == 200 and api.bodies[-1] == [ev("c")]


def test_after_a_refused_batch_the_next_heartbeat_is_bare_even_with_more_queued():
    api = RecordingApi(400)
    inst = supervisor(api)
    for i in range(60):
        inst._progress.add(ev(f"item-{i:02d}"))
    assert inst._beat() == 400 and inst._progress.pending() == 10  # the 50 sent are dropped
    assert inst._beat() == 200 and inst._beat() == 200
    assert api.bodies[1:] == [[], [ev(f"item-{i:02d}") for i in range(50, 60)]]


def test_a_refused_bare_heartbeat_is_not_retried_in_a_loop():
    api = RecordingApi(400, 400)
    inst = supervisor(api)
    inst._progress.add(ev("a"))
    assert inst._beat() == 400 and inst._bare_next
    assert inst._beat() == 400 and not inst._bare_next  # the bare one was refused too: back to normal beats
    inst._progress.add(ev("b"))
    assert inst._beat() == 200 and api.bodies == [[ev("a")], [], [ev("b")]]


@pytest.mark.parametrize("status", [0, 429, 503])
def test_a_transient_failure_keeps_the_batch_for_the_next_heartbeat(status):
    api = RecordingApi(status)
    inst = supervisor(api)
    inst._progress.add(ev("a"))
    assert inst._beat() == status
    assert inst._beat() == 200
    assert api.bodies == [[ev("a")], [ev("a")]]


def test_progress_left_at_exit_is_retried_through_a_busy_api():
    api = RecordingApi(429, 503, 200)  # 429: progress changed while the API was saving it
    inst = supervisor(api)
    inst._progress.add(ev("a", "done"))
    inst._flush_progress()
    assert api.bodies == [[ev("a", "done")]] * 3 and inst._progress.pending() == 0


def test_progress_left_at_exit_is_given_up_after_three_tries_or_a_409(capsys):
    for statuses, calls in (((503, 503, 503, 200), 3), ((409,), 1)):
        api = RecordingApi(*statuses)
        inst = supervisor(api)
        inst._progress.add(ev("a", "done"))
        inst._flush_progress()
        assert len(api.bodies) == calls and "1 progress events could not be sent" in capsys.readouterr().err


REPORT = """
    import json, os, signal, sys, time, urllib.error, urllib.request
    def report(item, state):
        req = urllib.request.Request(os.environ["BURN_PROGRESS_URL"], method="POST",
                                     data=json.dumps({"item": item, "state": state}).encode(),
                                     headers={"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"]})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read()), time.time()
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read()), time.time()
"""


def timed(stack):
    """Every request the fake API answers, with when it arrived."""
    seen, original = [], stack.handle

    def handle(method, path, query, body):
        seen.append((time.time(), method, path, body))
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
    time.sleep(3)"""
    started = time.time()
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.25).run() == 0
    beats = progress_beats(seen)
    assert [e for _, batch in beats for e in batch] == [
        ev(f"item-{i:02d}", state) for i in range(1, 4) for state in ("started", "done")]
    assert beats[0][0] - started < 20  # heartbeat_every is a minute: progress itself caused these beats
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
    out = stack.tmp / "replies.json"
    code = REPORT + f"""
    replies = [report("bad-item", "started")[:2]]
    time.sleep(0.6)
    replies.append(report("good-item", "started")[:2])
    json.dump(replies, open({str(out)!r}, "w"))"""
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1).run() == 0
    assert [batch for _, batch in progress_beats(seen)] == [[ev("bad-item")], [ev("good-item")]]
    (bad_status, bad), (good_status, good) = json.loads(out.read_text())
    assert bad_status == 400 and "at most 500 items per instance" in bad["error"]  # the agent must not start it
    assert (good_status, good) == (200, {"queued": True, "recorded": True})
    assert "refused 1 progress events" in capfd.readouterr().err
    assert next(iter(stack.instances.values()))["status"] == "exited"


def test_a_refused_heartbeat_is_followed_promptly_by_a_bare_one_that_keeps_the_instance_alive(stack):
    original = stack.handle

    def refuse_progress(method, path, query, body):
        if path.endswith("/heartbeat") and body.get("progress"):
            return 400, {"error": "item id must match ^[A-Za-z0-9_.:-]{1,64}$"}
        if path.endswith("/heartbeat"):
            stack.instances[path.split("/")[2]]["last_seen"] = "alive"
        return original(method, path, query, body)

    stack.handle = refuse_progress
    seen = timed(stack)
    code = REPORT + """
    report("item-01", "started")
    time.sleep(1.5)"""
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.2).run() == 0
    beats = [(t, body.get("progress")) for t, _, path, body in seen if path.endswith("/heartbeat")]
    refused = next(i for i, (_, progress) in enumerate(beats) if progress)
    then, bare = beats[refused][0], beats[refused + 1]
    assert bare[1] is None and 0.15 <= bare[0] - then < 20  # after the minimum gap, not a minute later
    assert next(iter(stack.instances.values()))["last_seen"] == "alive"


def test_a_409_still_means_burned_and_the_waiting_start_is_told_so(stack):
    stack.heartbeat_status = 409
    out = stack.tmp / "reply.json"
    code = REPORT + f"""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)  # so it lives long enough to write down what it was told
    json.dump(report("item-01", "started")[:2], open({str(out)!r}, "w"))
    time.sleep(30)"""
    started = time.monotonic()
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1, stop_grace=1).run() == EXIT_STOPPED
    assert time.monotonic() - started < 10
    status, reply = json.loads(out.read_text())
    assert status == 409 and "burned" in reply["error"]


def test_the_agent_hears_of_its_start_only_after_burn_received_it(stack):
    seen = timed(stack)
    out = stack.tmp / "reply.json"
    code = REPORT + f"""
    json.dump(report("item-01", "started"), open({str(out)!r}, "w"))"""
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1).run() == 0
    status, reply, answered_at = json.loads(out.read_text())
    assert (status, reply) == (200, {"queued": True, "recorded": True})
    (received_at, _), = progress_beats(seen)
    assert received_at <= answered_at


def test_a_start_behind_more_than_50_queued_events_still_goes_50_at_a_time(stack):
    seen = timed(stack)
    out = stack.tmp / "reply.json"
    code = REPORT + f"""
    for i in range(60):
        report(f"item-{{i:02d}}", "done")
    json.dump(report("last", "started")[:2], open({str(out)!r}, "w"))"""
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1).run() == 0
    assert json.loads(out.read_text()) == [200, {"queued": True, "recorded": True}]
    batches = [batch for _, batch in progress_beats(seen)]
    assert all(len(batch) <= 50 for batch in batches)
    assert [e for batch in batches for e in batch] == [ev(f"item-{i:02d}", "done") for i in range(60)] + [ev("last")]


# -- the task and resume point a launcher hands over -------------------------------------------------
TASK = ["w01-item-01", "w01-item-02", "w01-item-03"]
RESUME = {"done": ["w01-item-01"], "quarantined": ["w01-item-02"]}


def test_a_valid_task_and_resume_point_reach_the_agent(stack):
    out = stack.tmp / "env.json"
    inst = instance(stack, py(DUMP_ENV, str(out)))
    inst.host_env.update({"BURN_TASK": json.dumps(TASK, indent=2), "BURN_RESUME": json.dumps(RESUME)})
    inst = Instance(inst.api, "worker", inst.command, gateway_url=inst.gateway_url, host_env=inst.host_env,
                    enroll_token=stack.token, sleep=lambda _: None)
    assert inst.run() == 0
    env = json.loads(out.read_text())
    assert json.loads(env["BURN_TASK"]) == TASK and json.loads(env["BURN_RESUME"]) == RESUME
    assert env["BURN_TASK"] == json.dumps(TASK)  # re-encoded from what was checked, not copied as given
    assert env["BURN_PROGRESS_URL"].startswith("http://127.0.0.1:") and env["BURN_PROGRESS_URL"].endswith("/progress")


@pytest.mark.parametrize("name", ["BURN_TASK", "BURN_RESUME"])
def test_no_task_means_none_in_the_agent_env(stack, name):
    out = stack.tmp / "env.json"
    assert instance(stack, py(DUMP_ENV, str(out))).run() == 0
    assert name not in json.loads(out.read_text())


def test_an_empty_task_variable_counts_as_unset():
    assert task_values({"BURN_TASK": "", "BURN_RESUME": ""}) == {}


@pytest.mark.parametrize("name,value,message", [
    ("BURN_TASK", "item-01,item-02", "BURN_TASK is not valid JSON"),
    ("BURN_TASK", '"item-01"', "BURN_TASK must be a list of item ids"),
    ("BURN_TASK", '{"items": ["a"]}', "BURN_TASK must be a list of item ids"),
    ("BURN_TASK", '["ok", "rm -rf /"]', "BURN_TASK must be a list of item ids"),
    ("BURN_TASK", '["ok", 7]', "BURN_TASK must be a list of item ids"),
    ("BURN_TASK", '[["nested"]]', "BURN_TASK must be a list of item ids"),
    ("BURN_TASK", json.dumps([f"i{n}" for n in range(501)]), "more than 500 items"),
    ("BURN_TASK", json.dumps(["bst_" + "A" * 43]), "looks like a session token"),
    ("BURN_RESUME", '["a"]', 'BURN_RESUME must be {"done": [...], "quarantined": [...]}'),
    ("BURN_RESUME", '{"done": ["a"]}', 'BURN_RESUME must be {"done": [...], "quarantined": [...]}'),
    ("BURN_RESUME", '{"done": [], "quarantined": [], "note": "trust me"}', 'BURN_RESUME must be'),
    ("BURN_RESUME", '{"done": ["a b"], "quarantined": []}', "BURN_RESUME.done must be a list of item ids"),
    ("BURN_RESUME", '{"done": [], "quarantined": "a"}', "BURN_RESUME.quarantined must be a list of item ids"),
])
def test_anything_but_item_ids_is_refused(name, value, message):
    with pytest.raises(BadTask, match=re.escape(message)):
        task_values({name: value})


def test_burn_run_refuses_to_start_with_a_bad_task_before_it_joins(stack, capsys, monkeypatch):
    monkeypatch.setenv("BURN_TASK", '["item-01", "$(curl evil)"]')
    assert cli(["run", "--role", "worker", "--", "true"]) == 2
    err = capsys.readouterr().err
    assert "BURN_TASK must be a list of item ids" in err and "refusing to start" in err
    assert not stack.calls("POST", "/instances")  # no instance was started, so no join token use was spent


def test_a_child_instance_does_not_inherit_the_task(stack, monkeypatch):
    inst = instance(stack, py("pass"))
    inst = Instance(inst.api, "worker", inst.command, gateway_url=inst.gateway_url,
                    host_env={**inst.host_env, "BURN_TASK": json.dumps(TASK), "BURN_RESUME": json.dumps(RESUME)},
                    enroll_token=stack.token, sleep=lambda _: None)
    parent = stack.add_instance("sess_parent", "worker")
    inst.session = {"session_id": "sess_parent", "agent_id": parent["agent_id"]}
    inst._token = stack.session_tokens["sess_parent"]
    started = []
    monkeypatch.setattr(runmod.subprocess, "Popen", lambda argv, env=None, **kw: started.append(env) or REAL_POPEN(["true"]))
    inst.spawn_child("worker", ["true"])
    inst._stop_children()
    (child_supervisor_env,) = started
    assert "BURN_TASK" not in child_supervisor_env and "BURN_RESUME" not in child_supervisor_env


def test_the_agent_never_gets_the_session_admin_or_person_credentials(stack):
    out = stack.tmp / "env.json"
    inst = instance(stack, py(REPORT + """
    report("item-01", "done")
    json.dump(dict(os.environ), open(sys.argv[1], "w"))""", str(out)))
    inst.host_env.update({"BURN_TASK": json.dumps(TASK), "BURN_PEOPLE_TOKEN": "person-" + "p" * 30})
    inst = Instance(inst.api, "worker", inst.command, gateway_url=inst.gateway_url, host_env=inst.host_env,
                    enroll_token=stack.token, sleep=lambda _: None)
    assert inst.run() == 0
    blob = out.read_text()
    session_token = stack.issued[-1]["session_token"]
    assert session_token not in blob and API_TOKEN not in blob and stack.token not in blob and "person-ppp" not in blob
    beats = [r for r in stack.requests if r[1].endswith("/heartbeat") and r[2].get("progress")]
    assert beats and all(r[3]["Authorization"] == f"Bearer {session_token}" for r in beats)  # only the supervisor holds it
    assert inst._token == session_token and session_token not in json.dumps(inst.host_env)


def test_burn_env_lists_the_task_variables_as_passed_on():
    lines = "\n".join(describe({"BURN_TASK": json.dumps(TASK), "GITHUB_TOKEN": "ghp_x"}))
    removed = lines.split("# kept")[0]
    assert "GITHUB_TOKEN" in removed and "BURN_TASK" not in removed
    assert "BURN_PROGRESS_URL=http://127.0.0.1:<port>/progress" in lines


def test_the_help_lists_the_progress_endpoint_and_the_task_variables(capsys):
    with pytest.raises(SystemExit):
        cli(["run", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "BURN_PROGRESS_URL (POST {item, state}" in out and "BURN_TASK and BURN_RESUME" in out


def test_a_429_keeps_the_progress_for_the_next_heartbeat_and_is_not_a_burn(stack, capfd):
    original, busy = stack.handle, []

    def busy_once(method, path, query, body):
        if path.endswith("/heartbeat") and body.get("progress") and not busy:
            busy.append(1)
            return 429, {"error": "progress changed while it was being saved; retry"}
        return original(method, path, query, body)

    stack.handle = busy_once
    seen = timed(stack)
    out = stack.tmp / "reply.json"
    code = REPORT + f"""
    json.dump(report("item-01", "started")[:2], open({str(out)!r}, "w"))"""
    # heartbeat_every is a minute: the retry comes from the waiting start, after the minimum gap
    assert instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1).run() == 0
    assert [batch for _, batch in progress_beats(seen)] == [[ev("item-01")], [ev("item-01")]]
    assert json.loads(out.read_text()) == [200, {"queued": True, "recorded": True}]
    assert "dropped" not in capfd.readouterr().err
    assert next(iter(stack.instances.values()))["status"] == "exited"


def test_a_bad_task_ends_a_session_the_launcher_already_started(stack, capsys, monkeypatch):
    status, reply = stack.handle("POST", "/instances", {}, {"role": "worker", "enroll_token": stack.token})
    launcher_secrets = reply.pop("secrets")
    monkeypatch.setenv("BURN_ADOPT_SECRETS", json.dumps(launcher_secrets))
    monkeypatch.setenv("BURN_RESUME", '{"done": ["a"], "quarantined": [], "extra": 1}')
    assert cli(["run", "--role", "worker", "--adopt", json.dumps(reply), "--", "true"]) == 2
    assert "BURN_RESUME must be" in capsys.readouterr().err
    (call,) = stack.calls("POST", f"/instances/{reply['session_id']}/exit")
    assert call[3]["Authorization"] == f"Bearer {launcher_secrets['session_token']}"
    assert stack.instances[reply["session_id"]]["status"] == "exited"  # ended now, not left to the idle sweep


A_JOIN_TOKEN = "bjt_" + "0123456789abcdef" + "." + "B" * 43  # 64 characters: shaped like an item id


@pytest.mark.parametrize("name,value", [
    ("BURN_TASK", json.dumps(["item-01", A_JOIN_TOKEN])),
    ("BURN_RESUME", json.dumps({"done": [A_JOIN_TOKEN], "quarantined": []})),
    ("BURN_RESUME", json.dumps({"done": [], "quarantined": [A_JOIN_TOKEN]})),
])
def test_a_join_token_is_refused_although_it_fits_the_item_id_rule(name, value):
    assert len(A_JOIN_TOKEN) == 64
    with pytest.raises(BadTask, match="looks like a join token") as caught:
        task_values({name: value})
    assert A_JOIN_TOKEN not in str(caught.value)


@pytest.mark.parametrize("where", ["admin", "enroll", "join_env"])
def test_the_admin_and_join_tokens_this_burn_run_holds_are_refused_as_items(where):
    secret = "plainsecretvalue-0123456789"  # item-id shaped, and not in any known token format
    host = {"BURN_TASK": json.dumps(["item-01", secret])}
    host.update({"admin": {"BURN_DEMO_TOKEN": secret}, "join_env": {"BURN_JOIN_TOKEN": secret}}.get(where, {}))
    with pytest.raises(BadTask, match="holds a credential") as caught:
        Instance(RecordingApi(), "worker", ["true"], gateway_url="http://gateway", host_env=host,
                 enroll_token=secret if where == "enroll" else "other-token")
    assert secret not in str(caught.value)
