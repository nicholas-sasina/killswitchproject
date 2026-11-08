"""The automation person: the account that scripts sign in as when they call the Burn API as a person.

The demo, the smoke and end-to-end checks and the swarm launcher's join-token call all get their request headers
from automation_headers(). It signs in with Cognito's AdminInitiateAuth (ADMIN_USER_PASSWORD_AUTH) using the AWS
credentials of whoever runs the script, then keeps the ID token in memory until a minute before it expires. The
token is never printed, logged or written to disk.

Local development (BURN_LOCAL_DEV=1, the mock servers) sends the demo token instead. So does a stack that has not
switched people sign-in on: until then the API accepts only the demo token. Whether it has is BURN_PEOPLE_ENABLED
("on"/"off", which `make env` writes to .env), else SSM /burn/people/enabled. When neither can be read the answer is
"off", so a stack that is off needs no AWS access at all; on a stack that is on, the demo token is refused with a 401.

boto3 is imported only when it is needed, so this module stays importable without it.
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Callable

from . import names

REFRESH_MARGIN = 60   # seconds: mint a new token when less than this remains
SWITCH_RECHECK = 300  # seconds: how long whether the stack requires sign-in is remembered


class AutomationAuthError(Exception):
    """Signing in as the automation person failed. The message never contains a credential value."""


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", "")) or type(exc).__name__


def _boto3_client(service: str):
    import boto3  # noqa: PLC0415 - only needed when a token is minted
    return boto3.client(service, region_name=names.REGION)


def secret_hash(username: str, client_id: str, client_secret: str) -> str:
    """Cognito's SECRET_HASH for a client that has a secret: base64(HMAC-SHA256(secret, username + client id))."""
    digest = hmac.new(client_secret.encode(), (username + client_id).encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _expires_at(id_token: str, fallback: float) -> float:
    """The ID token's own `exp` claim. ExpiresIn in the reply is the access token's lifetime, which may differ."""
    try:
        payload = id_token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return fallback


class AutomationPerson:
    """Mints and caches the automation person's ID token. One per process is enough (see automation_headers)."""

    def __init__(self, client: Callable[[str], object] = _boto3_client, now: Callable[[], float] = time.time,
                 env: dict | None = None):
        self._client, self._now, self._env = client, now, env
        self._clients: dict[str, object] = {}
        self._lock = threading.RLock()
        self._token, self._expires_at = "", 0.0
        self._sign_in_on, self._switch_checked_until = False, 0.0

    def __repr__(self) -> str:
        return f"AutomationPerson(signed_in={bool(self._token)})"

    def _aws(self, service: str):
        if service not in self._clients:
            self._clients[service] = self._client(service)
        return self._clients[service]

    def _environ(self) -> dict:
        return os.environ if self._env is None else self._env

    def headers(self, demo_token: str | Callable[[], str] | None = None, env: dict | None = None) -> dict:
        """The header that authenticates a person-side call: the ID token, or the demo token (see the module doc).

        `demo_token` is the demo token or a function returning it. By default it is BURN_DEMO_TOKEN, else (outside
        local development) the stack's demo-token secret. `env` replaces os.environ for the settings, for a script
        that reads .env into a dict of its own.
        """
        environ = self._environ() if env is None else env
        if environ.get(names.LOCAL_DEV_ENV) == "1":
            return {names.DEMO_TOKEN_HEADER: self._demo(demo_token, environ, local=True)}
        token = self.id_token(environ)
        if not token:  # this stack does not require sign-in yet
            return {names.DEMO_TOKEN_HEADER: self._demo(demo_token, environ, local=False)}
        return {"authorization": f"Bearer {token}"}

    def _demo(self, demo_token, environ, *, local: bool) -> str:
        if callable(demo_token):
            demo_token = demo_token()
        value = demo_token or environ.get("BURN_DEMO_TOKEN", "")
        if not value and local:
            raise AutomationAuthError(f"{names.LOCAL_DEV_ENV}=1 needs BURN_DEMO_TOKEN (any value for the mock servers)")
        if not value:
            try:
                value = self._aws("secretsmanager").get_secret_value(SecretId=names.DEMO_TOKEN_SECRET)["SecretString"]
            except Exception as exc:  # noqa: BLE001 - reported without the response body
                raise AutomationAuthError(f"sign-in is off and there is no demo token ({_error_code(exc)})") from None
        return value

    def id_token(self, env: dict | None = None) -> str:
        """The automation person's ID token, minted when there is none or it is about to expire.

        "" while the stack does not require sign-in: the API would not accept the token yet.
        """
        with self._lock:
            if not self.requires_sign_in(env):
                self._token = ""
                return ""
            now = self._now()
            if self._token and now < self._expires_at - REFRESH_MARGIN:
                return self._token
            self._token, self._expires_at = self._mint(*self._login(), now)
            return self._token

    def requires_sign_in(self, env: dict | None = None) -> bool:
        """Whether the stack has switched people sign-in on: BURN_PEOPLE_ENABLED, else SSM, else "off"."""
        value = ((self._environ() if env is None else env).get(names.PEOPLE_ENABLED_ENV) or "").strip()
        if value:
            return value == "on"
        with self._lock:
            now = self._now()
            if now >= self._switch_checked_until:
                try:
                    value = self._aws("ssm").get_parameter(Name=names.PEOPLE_ENABLED_PARAM)["Parameter"]["Value"]
                except Exception:  # noqa: BLE001 - not published, no AWS access, or no boto3: the API then needs the demo token
                    value = "off"
                self._sign_in_on, self._switch_checked_until = value == "on", now + SWITCH_RECHECK
            return self._sign_in_on

    def _login(self) -> tuple[str, str, str]:
        """(username, password, client secret or "") from the automation person's secret."""
        try:
            raw = self._aws("secretsmanager").get_secret_value(SecretId=names.AUTOMATION_PERSON_SECRET)["SecretString"]
        except Exception as exc:  # noqa: BLE001
            raise AutomationAuthError(f"cannot read the automation person's secret ({_error_code(exc)})") from None
        try:
            secret = json.loads(raw)
            return secret["username"], secret["password"], secret.get("client_secret", "")
        except (ValueError, KeyError, TypeError):
            raise AutomationAuthError('the automation person\'s secret is not {"username", "password", "client_secret"} '
                                      "JSON") from None

    def _mint(self, username: str, password: str, client_secret: str, now: float) -> tuple[str, float]:
        try:
            ssm, idp = self._aws("ssm"), self._aws("cognito-idp")
            pool = ssm.get_parameter(Name=names.PEOPLE_USER_POOL_ID_PARAM)["Parameter"]["Value"]
            client_id = ssm.get_parameter(Name=names.AUTOMATION_CLIENT_ID_PARAM)["Parameter"]["Value"]
            if not client_secret:  # the automation client is confidential: its secret is part of the sign-in
                client = idp.describe_user_pool_client(UserPoolId=pool, ClientId=client_id)["UserPoolClient"]
                client_secret = client.get("ClientSecret", "")
            params = {"USERNAME": username, "PASSWORD": password}
            if client_secret:
                params["SECRET_HASH"] = secret_hash(username, client_id, client_secret)
            reply = idp.admin_initiate_auth(UserPoolId=pool, ClientId=client_id, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                                            AuthParameters=params)
        except Exception as exc:  # noqa: BLE001
            raise AutomationAuthError(f"the automation person could not sign in ({_error_code(exc)})") from None
        result = reply.get("AuthenticationResult") or {}
        if not result.get("IdToken"):
            raise AutomationAuthError("the automation person could not sign in "
                                      f"(asked to answer {reply.get('ChallengeName') or 'a challenge'})")
        return result["IdToken"], _expires_at(result["IdToken"], now + int(result.get("ExpiresIn", 3600)))


_default = AutomationPerson()


def automation_headers(demo_token: str | Callable[[], str] | None = None, env: dict | None = None) -> dict:
    """Headers for a person-side Burn API call made by a script. See AutomationPerson.headers."""
    return _default.headers(demo_token, env)


def people_sign_in_on(env: dict | None = None) -> bool:
    """Whether the stack requires a person's sign-in. See AutomationPerson.requires_sign_in."""
    return _default.requires_sign_in(env)
