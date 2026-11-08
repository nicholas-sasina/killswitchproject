"""minter_client is shared by the threads of a scoped burn."""
import time
from concurrent.futures import ThreadPoolExecutor

import boto3

import minter_client

REAL_CALL_ASYNC = minter_client.call_async  # the env fixture is not used here, but keep the real one


def test_minter_client_is_built_once_when_many_threads_start_cold(monkeypatch):
    built = []

    class Client:
        def invoke(self, **kwargs):
            return {"StatusCode": 202}

    def slow_client(*args, **kwargs):
        built.append(1)
        time.sleep(0.2)
        return Client()

    monkeypatch.setattr(boto3, "client", slow_client)
    monkeypatch.setattr(minter_client, "_lambda", None)
    with ThreadPoolExecutor(10) as pool:
        list(pool.map(lambda _: REAL_CALL_ASYNC("DELETE", "/agents/agent-x/credentials"), range(10)))
    assert len(built) == 1
