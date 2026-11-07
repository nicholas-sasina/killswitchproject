import secrets

import pytest

from burn_agents.badge import BadgeToken, TokenRefused


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


def make(responses, clock):
    calls = []

    def post(url, **kw):
        calls.append(kw)
        return responses.pop(0)

    return BadgeToken("https://issuer.test/token", "cid", secrets.token_hex(8), "scope/x",
                      post=post, clock=lambda: clock[0]), calls


def test_token_is_minted_once_and_reused():
    clock = [0.0]
    tok = secrets.token_hex(8)
    badge, calls = make([FakeResp(body={"access_token": tok, "expires_in": 3600})], clock)
    assert badge.get() == tok and badge.get() == tok
    assert len(calls) == 1
    assert calls[0]["data"] == {"grant_type": "client_credentials", "scope": "scope/x"}


def test_token_refreshes_before_expiry():
    clock = [0.0]
    first, second = secrets.token_hex(8), secrets.token_hex(8)
    badge, calls = make([FakeResp(body={"access_token": first, "expires_in": 300}),
                         FakeResp(body={"access_token": second, "expires_in": 300})], clock)
    assert badge.get() == first
    clock[0] = 200          # still fresh (expires at 300, margin 60)
    assert badge.get() == first
    clock[0] = 250          # inside the refresh margin
    assert badge.get() == second
    assert len(calls) == 2


def test_refused_client_raises():
    badge, _ = make([FakeResp(status=400, body={"error": "invalid_client"})], [0.0])
    with pytest.raises(TokenRefused):
        badge.get()
