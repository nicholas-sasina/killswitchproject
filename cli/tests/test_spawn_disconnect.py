"""A burn stops the agent while it may be talking to the local spawn endpoint: waiting on a start, or still sending
its request. Its going away must be quiet: no exception escapes the handler, so nothing prints a traceback into the
terminal that shows the agent's output."""
import json
import socket
import struct
import sys
import time
import urllib.request

import pytest

from burn_cli.runner.progress import ProgressQueue
from burn_cli.runner.spawn_endpoint import SpawnEndpoint


@pytest.fixture
def endpoint():
    queue = ProgressQueue()
    server = SpawnEndpoint(lambda role, command: {}, "loc", progress=queue, progress_wait=10.0).start()
    raised = []
    server._server.handle_error = lambda request, address: raised.append(sys.exc_info()[1])  # what would print
    yield server, queue, raised
    server.stop()


def connect(server, head: bytes) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", server._server.server_port))
    sock.sendall(head)
    return sock


def reset(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # as a killed agent's socket goes
    sock.close()


def assert_quiet_and_serving(server, raised, capfd):
    time.sleep(0.3)  # the handler has finished with the gone agent
    assert raised == []
    err = capfd.readouterr().err
    assert "Traceback" not in err and "BrokenPipeError" not in err and "ConnectionResetError" not in err
    request = urllib.request.Request(server.progress_url, data=json.dumps({"item": "T-2", "state": "done"}).encode(),
                                     headers={"Authorization": "Bearer loc"})
    with urllib.request.urlopen(request, timeout=10) as reply:  # the next caller is served as before
        assert reply.status == 200


def test_an_agent_burned_while_its_start_waits_raises_nothing(endpoint, capfd):
    server, queue, raised = endpoint
    body = json.dumps({"item": "T-1", "state": "started"}).encode()
    sock = connect(server, b"POST /progress HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer loc\r\n"
                           b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    deadline = time.monotonic() + 5
    while not queue.waiting():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    reset(sock)
    queue.gone()  # its 409 now goes to an agent that is no longer there
    assert_quiet_and_serving(server, raised, capfd)


def test_an_agent_stopped_while_sending_its_request_raises_nothing(endpoint, capfd):
    server, queue, raised = endpoint
    sock = connect(server, b"POST /progress HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer loc\r\n"
                           b"Content-Length: 50\r\n\r\n{\"item\"")  # the rest of the body never comes
    time.sleep(0.1)
    reset(sock)
    assert_quiet_and_serving(server, raised, capfd)
    assert queue.pending() == 1  # only the "done" sent afterwards: the cut-off report was not queued
