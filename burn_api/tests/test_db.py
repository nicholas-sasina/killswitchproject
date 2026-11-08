"""db.resource(): one resource per thread, all from one boto3 session.

A session per thread held its own copy of the service models (about 10 MB each). With the 16 exposure-count
threads up, the 256 MB Lambda filled, and calls hung until they timed out.
"""
import threading

import boto3
import pytest


@pytest.fixture
def db_module():
    import db
    db.reset()
    yield db
    db.reset()


def test_threads_get_their_own_resource_from_one_session(db_module, monkeypatch):
    made = []
    real = boto3.session.Session

    def counting_session(*args, **kwargs):
        made.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(boto3.session, "Session", counting_session)
    resources, start = [], threading.Barrier(8)

    def worker():
        start.wait()  # all eight ask for their resource at once
        resources.append(db_module.resource())
        assert db_module.resource() is db_module.resource()  # cached per thread

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1, "every thread must share one session"
    assert len({id(r) for r in resources}) == 8, "each thread still gets a resource of its own"


def test_reset_forgets_the_session(db_module, monkeypatch):
    made = []
    real = boto3.session.Session
    monkeypatch.setattr(boto3.session, "Session", lambda *a, **k: made.append(1) or real(*a, **k))
    db_module.resource()
    db_module.reset()
    db_module.resource()
    assert len(made) == 2
