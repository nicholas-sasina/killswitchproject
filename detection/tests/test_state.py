from collections import OrderedDict
from decimal import Decimal
import secrets
from unittest.mock import Mock

import pytest
pytest.importorskip("boto3")

from proxy.state import AwsState


def test_session_cache_refetches_after_one_second_and_checks_owner(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("proxy.state.monotonic", lambda: now[0])
    state = AwsState.__new__(AwsState)
    state._sessions = OrderedDict()
    agent, session = "agent-" + secrets.token_hex(4), secrets.token_urlsafe(24)
    state.sessions = Mock()
    state.sessions.get_item.return_value = {"Item": {"agent_id": agent, "status": "active"}}
    assert state.session(agent, session)
    assert state.session(agent, session)
    assert not state.session("agent-" + secrets.token_hex(4), session)
    assert state.sessions.get_item.call_count == 1
    state.sessions.get_item.return_value = {"Item": {"agent_id": agent, "status": "revoked"}}
    now[0] = 1.01
    assert state.session(agent, session) is None
    assert state.sessions.get_item.call_count == 2


@pytest.mark.parametrize("expiry", [Decimal(1), "2000-01-01T00:00:00Z", "invalid"])
def test_expired_sessions_are_rejected(expiry):
    state = AwsState.__new__(AwsState)
    state._sessions = OrderedDict()
    agent = "agent-" + secrets.token_hex(4)
    state.sessions = Mock()
    state.sessions.get_item.return_value = {"Item": {"agent_id": agent, "status": "active", "expires_at": expiry}}
    assert state.session(agent, secrets.token_urlsafe(24)) is None


def test_fingerprint_refresh_paginates_and_does_not_reuse_stale_cache_on_failure(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("proxy.state.monotonic", lambda: now[0])
    state = AwsState.__new__(AwsState)
    state._refreshed = float("-inf")
    state._rows = {}
    fp = secrets.token_hex(32)
    state.fingerprints = Mock()
    state.fingerprints.scan.side_effect = [
        {"Items": [], "LastEvaluatedKey": {"fingerprint": fp}},
        {"Items": [{"fingerprint": fp, "status": "rotated"}]},
    ]
    assert state.lookup({fp})[fp]["status"] == "rotated"
    assert state.fingerprints.scan.call_count == 2
    assert state.lookup({fp})
    assert state.fingerprints.scan.call_count == 2
    now[0] = 2.01
    state.fingerprints.scan.side_effect = RuntimeError("dependency unavailable")
    with pytest.raises(RuntimeError):
        state.lookup({fp})
