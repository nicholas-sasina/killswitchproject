"""burn run and task progress: the local endpoint, the queue and its trip on the heartbeat."""
import pytest

from burn_contracts.progress import ProgressError
from burn_cli.runner.progress import ProgressQueue


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
