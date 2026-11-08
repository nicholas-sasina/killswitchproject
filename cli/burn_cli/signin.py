"""People sign-in: the authorization-code flow with PKCE against the people pool, and the token cache.

`burn login` opens the browser, receives the redirect on 127.0.0.1, and exchanges the code for tokens. The ID token
is what the API accepts (`Authorization: Bearer <id token>`); it is refreshed from the refresh token shortly before
it expires. Tokens are cached in a private file and are never printed.
"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

CALLBACK_PORT = 8976
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
SCOPE = "openid email profile"
REFRESH_MARGIN = 60  # seconds: refresh when the ID token has less than this left
CALLBACK_TIMEOUT = 180


class SignInError(Exception):
    pass


# -- PKCE ---------------------------------------------------------------------------------------
def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge). The verifier stays on this machine; only the challenge goes to the browser."""
    verifier = secrets.token_urlsafe(64)  # 86 characters, within RFC 7636's 43-128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def base_url(domain: str) -> str:
    """https://<domain> for a bare host; a full URL (tests, local fakes) is used as given."""
    return domain.rstrip("/") if "://" in domain else f"https://{domain}"


def authorize_url(domain: str, client_id: str, challenge: str, state: str) -> str:
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI, "scope": SCOPE,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    return f"{base_url(domain)}/oauth2/authorize?{query}"


# -- the redirect -------------------------------------------------------------------------------
def wait_for_code(state: str, port: int | None = None, timeout: float = CALLBACK_TIMEOUT,
                  started: threading.Event | None = None) -> str:
    """Serve 127.0.0.1:<port> until the redirect arrives; return its code. A wrong state is refused."""
    port = port or CALLBACK_PORT
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/callback":
                return self._reply(404, "Not found")
            query = urllib.parse.parse_qs(parsed.query)
            got_state = (query.get("state") or [""])[0]
            if not secrets.compare_digest(got_state.encode(), state.encode()):
                result["error"] = "the sign-in response did not match this sign-in attempt (state mismatch)"
                return self._reply(400, "Sign-in failed: this response does not belong to your sign-in attempt.")
            if "error" in query:
                result["error"] = f"sign-in was refused: {(query['error'] or [''])[0]}"
                return self._reply(400, "Sign-in was not completed. You can close this tab.")
            result["code"] = (query.get("code") or [""])[0]
            self._reply(200, "You are signed in to Burn. You can close this tab.")

        def _reply(self, status, text):
            data = f"<!doctype html><title>Burn</title><p>{text}</p>".encode()
            self.send_response(status)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise SignInError(f"cannot listen on 127.0.0.1:{port} for the sign-in redirect ({exc.strerror})") from None
    server.timeout = 1
    if started:
        started.set()
    deadline = time.monotonic() + timeout
    try:
        while "code" not in result and "error" not in result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise SignInError(result["error"])
    if not result.get("code"):
        raise SignInError("timed out waiting for the browser to finish signing in")
    return result["code"]


# -- the token endpoint -------------------------------------------------------------------------
def _token_request(domain: str, path: str, form: dict) -> dict:
    req = urllib.request.Request(base_url(domain) + path, method="POST", data=urllib.parse.urlencode(form).encode(),
                                 headers={"content-type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            reason = json.loads(err.read()).get("error", "")
        except ValueError:
            reason = ""
        # only the status and the OAuth error code: the response and the request carry tokens
        raise SignInError(f"the sign-in service refused the request (HTTP {err.code}" + (f", {reason}" if reason else "") + ")") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise SignInError(f"cannot reach the sign-in service ({getattr(exc, 'reason', exc)})") from None


def claims(token: str) -> dict:
    """The payload of a JWT, for display and expiry only. The API is what verifies the signature."""
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}


def _credentials(tokens: dict, domain: str, client_id: str, previous_refresh: str = "", now: float | None = None) -> dict:
    id_token = tokens.get("id_token")
    if not id_token:
        raise SignInError("the sign-in service returned no ID token")
    now = time.time() if now is None else now
    info = claims(id_token)
    expires_at = info.get("exp") or now + int(tokens.get("expires_in", 3600))
    return {"id_token": id_token, "refresh_token": tokens.get("refresh_token") or previous_refresh,
            "expires_at": int(expires_at), "email": info.get("email", ""),
            # not secret: lets a refresh work without the environment that signed in
            "domain": domain, "client_id": client_id}


def exchange_code(domain: str, client_id: str, code: str, verifier: str) -> dict:
    tokens = _token_request(domain, "/oauth2/token", {
        "grant_type": "authorization_code", "client_id": client_id, "code": code,
        "redirect_uri": REDIRECT_URI, "code_verifier": verifier})
    return _credentials(tokens, domain, client_id)


def refreshed(cached: dict) -> dict:
    if not cached.get("refresh_token"):
        raise SignInError("your session has expired: run `burn login`")
    try:
        tokens = _token_request(cached["domain"], "/oauth2/token", {
            "grant_type": "refresh_token", "client_id": cached["client_id"], "refresh_token": cached["refresh_token"]})
    except SignInError as exc:
        raise SignInError(f"{exc}; run `burn login` again") from None
    return _credentials(tokens, cached["domain"], cached["client_id"], previous_refresh=cached["refresh_token"])


# -- the cache ----------------------------------------------------------------------------------
def cache_path() -> Path:
    base = os.environ.get("BURN_CONFIG_DIR") or str(Path.home() / ".config" / "burn")
    return Path(base) / "credentials.json"


def save_cache(credentials: dict, path: Path | None = None) -> Path:
    """Write the cache so it is never readable by anyone else, not even briefly: the directory is 0700 and the file
    is created 0600 under a temporary name, then renamed into place."""
    path = path or cache_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp = path.parent / f".credentials.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(credentials))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def load_cache(path: Path | None = None) -> dict | None:
    try:
        data = json.loads((path or cache_path()).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id_token") else None


def delete_cache(path: Path | None = None) -> bool:
    try:
        (path or cache_path()).unlink()
        return True
    except FileNotFoundError:
        return False


# -- the flows ----------------------------------------------------------------------------------
def login(domain: str, client_id: str, open_browser: Callable[[str], bool] | None = None,
          say: Callable[[str], None] = print, port: int | None = None, timeout: float = CALLBACK_TIMEOUT) -> dict:
    open_browser = open_browser or webbrowser.open
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorize_url(domain, client_id, challenge, state)
    listening = threading.Event()
    result: dict = {}

    def listen():
        try:
            result["code"] = wait_for_code(state, port, timeout, started=listening)
        except SignInError as exc:
            result["error"] = exc
            listening.set()

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    listening.wait(5)
    if "error" not in result and not open_browser(url):
        say(f"Open this address in your browser to sign in:\n{url}")  # the URL holds no token
    while thread.is_alive():
        thread.join(0.5)
    if "error" in result:
        raise SignInError(str(result["error"]))
    credentials = exchange_code(domain, client_id, result["code"], verifier)
    save_cache(credentials)
    return credentials


def logout(revoke: bool = True) -> bool:
    cached = load_cache()
    if cached and revoke and cached.get("refresh_token"):
        try:
            _token_request(cached["domain"], "/oauth2/revoke", {"token": cached["refresh_token"], "client_id": cached["client_id"]})
        except (SignInError, KeyError):
            pass  # signing out locally is what matters; the token expires on its own
    return delete_cache()


def current_id_token(now: float | None = None) -> str:
    """The ID token to send as a bearer: from the cache, refreshed when it has under a minute left."""
    cached = load_cache()
    if cached is None:
        raise SignInError("not signed in: run `burn login`")
    now = time.time() if now is None else now
    if cached.get("expires_at", 0) - now < REFRESH_MARGIN:
        cached = refreshed(cached)
        save_cache(cached)
    return cached["id_token"]
