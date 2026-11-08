"""People sign-in: PKCE, state, the token cache, and refresh, against a fake sign-in service."""
import hashlib
import json
import os
import socket
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from burn_cli import signin
from fake_cognito import FakeCognito, jwt


@pytest.fixture
def cognito(tmp_path, monkeypatch):
    monkeypatch.setenv("BURN_CONFIG_DIR", str(tmp_path / "cfg" / "burn"))
    fake = FakeCognito()
    yield fake
    fake.close()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def browser(cognito, port, *, code="good-code", state=None, capture=None):
    """Plays the user's browser: reads the sign-in address, then follows the redirect to the local listener."""
    def open_url(url):
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        cognito.challenge = query["code_challenge"]
        if capture is not None:
            capture.update(query, url=url)
        back = urllib.parse.urlencode({"code": code, "state": state or query["state"]})
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{back}", timeout=5).read()
        except urllib.error.HTTPError as err:
            err.read()
        return True

    return open_url


def test_pkce_challenge_is_s256_of_a_fresh_verifier():
    verifier, challenge = signin.pkce_pair()
    assert 43 <= len(verifier) <= 128 and "=" not in challenge
    import base64
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert signin.pkce_pair()[0] != verifier


def test_the_authorize_address_has_the_required_parameters():
    url = signin.authorize_url("people.example.com", "client123", "chal", "st8")
    parts = urllib.parse.urlsplit(url)
    query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "people.example.com", "/oauth2/authorize")
    assert query == {"response_type": "code", "client_id": "client123", "redirect_uri": "http://localhost:8976/callback",
                     "scope": "openid email profile", "code_challenge": "chal", "code_challenge_method": "S256", "state": "st8"}


def test_login_runs_the_whole_flow_and_caches_private_credentials(cognito, capsys):
    port, seen = free_port(), {}
    credentials = signin.login(cognito.url, "client123", open_browser=browser(cognito, port, capture=seen), port=port)
    assert credentials["email"] == "ada@example.com"
    exchange = next(f for p, f in cognito.requests if f.get("grant_type") == "authorization_code")
    assert exchange["code"] == "good-code" and exchange["client_id"] == "client123"
    assert exchange["redirect_uri"] == "http://localhost:8976/callback"
    assert hashlib.sha256(exchange["code_verifier"].encode()).digest()  # the verifier is what proves the challenge
    assert seen["state"] and seen["code_challenge_method"] == "S256"
    cached = signin.load_cache()
    assert cached["id_token"] == credentials["id_token"] and cached["refresh_token"].startswith("refresh-")
    assert set(cached) >= {"id_token", "refresh_token", "expires_at", "email"}
    out = capsys.readouterr()
    assert credentials["id_token"] not in out.out + out.err and cached["refresh_token"] not in out.out + out.err


def callback(port, **query):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback?{urllib.parse.urlencode(query)}", timeout=5).read()
    except urllib.error.HTTPError as err:
        return err.code
    return 200


def test_a_redirect_without_this_attempts_state_is_ignored_and_cannot_cancel_the_sign_in(cognito):
    port, statuses = free_port(), []
    real = browser(cognito, port)

    def forged_then_real(url):
        statuses.append(callback(port, code="forged-code", state="forged"))  # e.g. a web page aimed at localhost
        statuses.append(callback(port, error="access_denied"))               # no state at all
        return real(url)

    credentials = signin.login(cognito.url, "c", open_browser=forged_then_real, port=port)
    assert statuses == [400, 400] and credentials["email"] == "ada@example.com"
    exchanged = [f["code"] for p, f in cognito.requests if f.get("grant_type") == "authorization_code"]
    assert exchanged == ["good-code"]


def test_only_a_forged_redirect_times_out_with_nothing_exchanged_or_cached(cognito):
    port = free_port()
    with pytest.raises(signin.SignInError, match="timed out"):
        signin.login(cognito.url, "c", open_browser=browser(cognito, port, state="forged"), port=port, timeout=1.5)
    assert not [f for p, f in cognito.requests if p == "/oauth2/token"] and signin.load_cache() is None


def test_a_reply_with_this_state_but_no_code_is_a_clear_error(cognito):
    port = free_port()

    def no_code(url):
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        callback(port, state=state)
        return True

    with pytest.raises(signin.SignInError, match="had no authorization code"):
        signin.login(cognito.url, "c", open_browser=no_code, port=port, timeout=5)


@pytest.mark.parametrize("then_sign_in", [False, True])
def test_an_idle_connection_cannot_hold_the_listener_past_its_deadline(cognito, monkeypatch, then_sign_in):
    monkeypatch.setattr(signin, "CONNECTION_TIMEOUT", 0.5)
    port, idle = free_port(), []
    real = browser(cognito, port)

    def idle_then_maybe_real(url):
        idle.append(socket.create_connection(("127.0.0.1", port)))  # connects and never sends a byte
        return real(url) if then_sign_in else True

    started = time.monotonic()
    try:
        if then_sign_in:
            assert signin.login(cognito.url, "c", open_browser=idle_then_maybe_real, port=port, timeout=10)["email"]
        else:
            with pytest.raises(signin.SignInError, match="timed out"):
                signin.login(cognito.url, "c", open_browser=idle_then_maybe_real, port=port, timeout=1.5)
    finally:
        for sock in idle:
            sock.close()
    assert time.monotonic() - started < (8 if then_sign_in else 1.5 + 0.5 + 2)


def test_a_refused_sign_in_and_a_rejected_code_are_clear_errors(cognito):
    port = free_port()
    with pytest.raises(signin.SignInError, match="refused the request \\(HTTP 400, invalid_grant\\)"):
        signin.login(cognito.url, "c", open_browser=browser(cognito, port, code="stolen-code"), port=port)
    assert signin.load_cache() is None


def test_the_sign_in_address_is_shown_when_no_browser_opens(cognito):
    port, said = free_port(), []
    inner = browser(cognito, port)
    signin.login(cognito.url, "c", open_browser=lambda url: inner(url) and False, say=said.append, port=port)
    assert said and said[0].startswith("Open this address") and "/oauth2/authorize?" in said[0]


def test_the_cache_is_private_from_the_first_moment(cognito, monkeypatch):
    seen = []
    real_replace = os.replace

    def watching_replace(src, dst):
        seen.append(stat.S_IMODE(os.stat(src).st_mode))  # the file's mode before it appears under its real name
        return real_replace(src, dst)

    monkeypatch.setattr(signin.os, "replace", watching_replace)
    old = os.umask(0)
    try:
        path = signin.save_cache({"id_token": "x", "refresh_token": "y", "expires_at": 1, "email": "a@b"})
    finally:
        os.umask(old)
    assert seen == [0o600] and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == ["credentials.json"]  # no temporary file left behind


def test_a_new_directory_is_private_and_an_existing_one_keeps_its_mode(cognito, monkeypatch):
    old = os.umask(0)  # the 0700 must not depend on the umask
    try:
        path = signin.save_cache({"id_token": "x"})
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    shared = path.parent.parent / "shared"  # a BURN_CONFIG_DIR that already exists for other things
    shared.mkdir()
    shared.chmod(0o755)
    monkeypatch.setenv("BURN_CONFIG_DIR", str(shared))
    path = signin.save_cache({"id_token": "x"})
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755 and stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_refreshed_sign_in_that_cannot_be_saved_is_a_sign_in_error(cognito, monkeypatch):
    cognito.refresh_tokens.add("refresh-0")
    signin.save_cache({"id_token": jwt({"exp": 1}), "refresh_token": "refresh-0", "expires_at": 1,
                       "email": "a@example.com", "domain": cognito.url, "client_id": "c"})

    def full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(signin, "save_cache", full)
    with pytest.raises(signin.SignInError, match="could not save the refreshed sign-in .*No space left on device"):
        signin.current_id_token()


def test_a_token_with_plenty_of_time_left_is_used_as_is(cognito):
    cognito.refresh_tokens.add("r")
    signin.save_cache({"id_token": jwt({"exp": int(time.time()) + 3600}), "refresh_token": "r",
                       "expires_at": int(time.time()) + 3600, "email": "a@b", "domain": cognito.url, "client_id": "c"})
    token = signin.current_id_token()
    assert token == signin.load_cache()["id_token"] and cognito.requests == []


def test_an_expiring_token_is_refreshed_and_the_refresh_token_kept(cognito):
    cognito.refresh_tokens.add("old-refresh")
    signin.save_cache({"id_token": "stale", "refresh_token": "old-refresh", "expires_at": int(time.time()) + 30,
                       "email": "ada@example.com", "domain": cognito.url, "client_id": "c"})
    token = signin.current_id_token()  # under 60 seconds left
    assert token != "stale" and signin.claims(token)["email"] == "ada@example.com"
    (path, form), = cognito.requests
    assert form == {"grant_type": "refresh_token", "client_id": "c", "refresh_token": "old-refresh"}
    cached = signin.load_cache()
    assert cached["id_token"] == token and cached["refresh_token"] == "old-refresh"  # Cognito sent no new one
    assert cached["expires_at"] > time.time() + 3000
    assert signin.current_id_token() == token and len(cognito.requests) == 1  # fresh again: no second refresh


def test_an_expired_session_that_cannot_refresh_says_to_sign_in_again(cognito):
    signin.save_cache({"id_token": "stale", "refresh_token": "revoked", "expires_at": 1, "email": "a@b",
                       "domain": cognito.url, "client_id": "c"})
    with pytest.raises(signin.SignInError, match="run `burn login` again") as exc:
        signin.current_id_token()
    assert "revoked" not in str(exc.value) and "stale" not in str(exc.value)  # no token in the message


def test_not_signed_in_is_a_clear_error(cognito):
    with pytest.raises(signin.SignInError, match="not signed in: run `burn login`"):
        signin.current_id_token()


def test_logout_revokes_the_refresh_token_and_deletes_the_cache(cognito):
    port = free_port()
    credentials = signin.login(cognito.url, "c", open_browser=browser(cognito, port), port=port)
    assert signin.logout() is True
    assert cognito.revoked == [credentials["refresh_token"]] and signin.load_cache() is None
    assert signin.logout() is False  # nothing left to delete


def test_logout_still_signs_out_locally_when_revoking_fails(cognito):
    signin.save_cache({"id_token": "x", "refresh_token": "r", "expires_at": 1, "email": "a@b",
                       "domain": "http://127.0.0.1:1", "client_id": "c"})
    assert signin.logout() is True and signin.load_cache() is None
