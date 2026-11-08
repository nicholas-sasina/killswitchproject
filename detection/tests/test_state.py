from collections import OrderedDict
from decimal import Decimal
import secrets
import time
from unittest.mock import Mock

import pytest
pytest.importorskip("boto3")

from burn_contracts import names
from burn_contracts.events import EVENT_RETENTION_DAYS, OPTIONAL_FIELDS, TTL_ATTRIBUTE
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


def test_an_egress_event_is_stored_like_every_other_event():
    state = AwsState.__new__(AwsState)
    state.events = Mock()
    agent = "agent-" + secrets.token_hex(4)
    event_id = state.emit(agent_id=agent, action="blocked", session_id=secrets.token_hex(8),
                          destination="paste.example", detail="credential in request body")
    [call] = state.events.put_item.call_args_list
    row = call.kwargs["Item"]
    assert row["stream"] == names.EVENTS_STREAM and row["event_id"] == event_id and row["source"] == "egress"
    assert set(OPTIONAL_FIELDS) <= set(row) and row["flag_level"] is None  # unused fields are kept
    ttl = row[TTL_ATTRIBUTE]  # so DynamoDB expires egress events too
    assert type(ttl) is int and abs(ttl - (time.time() + EVENT_RETENTION_DAYS * 86400)) < 60
