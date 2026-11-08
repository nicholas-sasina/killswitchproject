"""The automation person's sign-in: mint, cache, refresh, the demo-token paths, and no token in any output."""
import json

import pytest

from burn_contracts import automation, names
from burn_contracts.automation import AutomationAuthError, AutomationPerson

POOL, CLIENT = "us-east-1_Pool", "automation-client"
LOGIN = {"username": "automation", "password": "automation-password-value"}


class NotFound(Exception):
    response = {"Error": {"Code": "ResourceNotFoundException", "Message": "secret value automation-password-value"}}


class ParameterNotFound(Exception):
    response = {"Error": {"Code": "ParameterNotFound"}}


class Aws:
    """Secrets Manager, SSM and Cognito, as far as the helper uses them. Records every call."""

    def __init__(self, secrets=None, lifetime=3600, challenge=None, client_secret="client-secret-value", enabled="on"):
        self.secrets = {names.AUTOMATION_PERSON_SECRET: json.dumps(LOGIN), names.DEMO_TOKEN_SECRET: "stack-demo"}
        self.secrets.update(secrets or {})
        self.params = {names.PEOPLE_USER_POOL_ID_PARAM: POOL, names.AUTOMATION_CLIENT_ID_PARAM: CLIENT,
                       names.PEOPLE_ENABLED_PARAM: enabled}
        self.lifetime, self.challenge, self.calls, self.minted = lifetime, challenge, [], 0
        self.client_secret = client_secret

    def __call__(self, service):
        return self

    def get_secret_value(self, SecretId):
        self.calls.append(("secret", SecretId))
        if self.secrets.get(SecretId) is None:
            raise NotFound()
        return {"SecretString": self.secrets[SecretId]}

    def get_parameter(self, Name):
        self.calls.append(("param", Name))
        if self.params.get(Name) is None:
            raise ParameterNotFound()
        return {"Parameter": {"Value": self.params[Name]}}

    def describe_user_pool_client(self, UserPoolId, ClientId):
        self.calls.append(("describe", ClientId))
        client = {"ClientId": ClientId, "UserPoolId": UserPoolId}
        return {"UserPoolClient": {**client, "ClientSecret": self.client_secret} if self.client_secret else client}

    def admin_initiate_auth(self, **kw):
        self.calls.append(("auth", kw))
        if self.challenge:
            return {"ChallengeName": self.challenge, "Session": "s"}
        self.minted += 1
        return {"AuthenticationResult": {"IdToken": f"id-token-{self.minted}", "ExpiresIn": self.lifetime}}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def person(aws, env=None, clock=None):
    return AutomationPerson(client=aws, now=clock or Clock(), env={} if env is None else env)


def test_mints_with_admin_user_password_auth_and_sends_the_id_token():
    aws = Aws()
    assert person(aws).headers() == {"authorization": "Bearer id-token-1"}
    (auth,) = [c[1] for c in aws.calls if c[0] == "auth"]
    assert auth == {"UserPoolId": POOL, "ClientId": CLIENT, "AuthFlow": "ADMIN_USER_PASSWORD_AUTH",
                    "AuthParameters": {"USERNAME": "automation", "PASSWORD": LOGIN["password"],
                                       "SECRET_HASH": automation.secret_hash("automation", CLIENT, "client-secret-value")}}


def test_secret_hash_is_cognitos_hmac_of_username_and_client_id():
    # base64(HMAC-SHA256(key="secret", msg="userclient")), from `openssl dgst -sha256 -hmac secret -binary`
    assert automation.secret_hash("user", "client", "secret") == "wvW87lzZoI+qQCVGmWVBJLlucdJ65huAVP1z+0MgA6E="


def test_a_client_secret_in_the_automation_secret_is_used_without_describing_the_client():
    aws = Aws(secrets={names.AUTOMATION_PERSON_SECRET: json.dumps({**LOGIN, "client_secret": "from-secret"})})
    person(aws).headers()
    (auth,) = [c[1] for c in aws.calls if c[0] == "auth"]
    assert auth["AuthParameters"]["SECRET_HASH"] == automation.secret_hash("automation", CLIENT, "from-secret")
    assert not [c for c in aws.calls if c[0] == "describe"]


def test_a_client_without_a_secret_signs_in_without_a_secret_hash():
    aws = Aws(client_secret="")
    assert person(aws).headers() == {"authorization": "Bearer id-token-1"}
    (auth,) = [c[1] for c in aws.calls if c[0] == "auth"]
    assert "SECRET_HASH" not in auth["AuthParameters"]


def test_the_token_is_cached_until_a_minute_before_it_expires_then_minted_again():
    aws, clock = Aws(lifetime=3600), Clock()
    p = person(aws, clock=clock)
    assert p.id_token() == p.id_token() == "id-token-1"
    clock.t += 3600 - automation.REFRESH_MARGIN - 1
    assert p.id_token() == "id-token-1" and aws.minted == 1
    clock.t += 2
    assert p.id_token() == "id-token-2" and aws.minted == 2


@pytest.mark.parametrize("enabled", ["off", None])  # switched off, or a stack from before sign-in
def test_until_the_stack_requires_sign_in_it_sends_the_demo_token(enabled):
    aws = Aws(enabled=enabled)  # the automation person may already exist: the API would not accept its token yet
    assert person(aws, env={"BURN_DEMO_TOKEN": "env-demo"}).headers() == {names.DEMO_TOKEN_HEADER: "env-demo"}
    assert person(aws).headers() == {names.DEMO_TOKEN_HEADER: "stack-demo"}  # else the stack's demo-token secret
    assert person(aws).headers(demo_token=lambda: "given") == {names.DEMO_TOKEN_HEADER: "given"}
    assert not [c for c in aws.calls if c[0] == "auth" or c == ("secret", names.AUTOMATION_PERSON_SECRET)]


def test_the_switch_is_rechecked_now_and_then_not_on_every_call():
    aws, clock = Aws(enabled="off"), Clock()
    p = person(aws, env={"BURN_DEMO_TOKEN": "d"}, clock=clock)
    p.headers(), p.headers()
    assert aws.calls.count(("param", names.PEOPLE_ENABLED_PARAM)) == 1
    aws.params[names.PEOPLE_ENABLED_PARAM] = "on"  # the stack flips
    clock.t += automation.SWITCH_RECHECK + 1
    assert p.headers() == {"authorization": "Bearer id-token-1"}


def test_sign_in_on_without_an_automation_person_is_an_error_not_a_silent_fallback():
    aws = Aws(secrets={names.AUTOMATION_PERSON_SECRET: None})
    with pytest.raises(AutomationAuthError, match=r"cannot read the automation person's secret \(ResourceNotFoundException\)"):
        person(aws, env={"BURN_DEMO_TOKEN": "d"}).headers()


def test_an_unreadable_switch_is_an_error():
    class Denied(Exception):
        response = {"Error": {"Code": "AccessDeniedException"}}

    aws = Aws()
    aws.get_parameter = lambda Name: (_ for _ in ()).throw(Denied())
    with pytest.raises(AutomationAuthError, match=r"requires sign-in \(AccessDeniedException\)"):
        person(aws).headers()


def test_local_development_sends_the_demo_token_and_makes_no_aws_call():
    aws = Aws()
    env = {names.LOCAL_DEV_ENV: "1", "BURN_DEMO_TOKEN": "mock"}
    assert person(aws, env=env).headers() == {names.DEMO_TOKEN_HEADER: "mock"}
    assert aws.calls == []
    with pytest.raises(AutomationAuthError, match="needs BURN_DEMO_TOKEN"):
        person(aws, env={names.LOCAL_DEV_ENV: "1"}).headers()
    assert aws.calls == []


@pytest.mark.parametrize("aws", [Aws(challenge="NEW_PASSWORD_REQUIRED"),
                                 Aws(secrets={names.AUTOMATION_PERSON_SECRET: "automation-password-value"})])
def test_a_failed_sign_in_never_shows_the_password(aws):
    with pytest.raises(AutomationAuthError) as exc:
        person(aws).headers()
    assert LOGIN["password"] not in str(exc.value) and exc.value.__cause__ is None


def test_an_aws_error_is_reported_by_code_only():
    class Denied(Exception):
        response = {"Error": {"Code": "AccessDeniedException", "Message": "automation-password-value"}}

    aws = Aws()
    aws.admin_initiate_auth = lambda **kw: (_ for _ in ()).throw(Denied("automation-password-value"))
    with pytest.raises(AutomationAuthError, match=r"could not sign in \(AccessDeniedException\)") as exc:
        person(aws).headers()
    assert "automation-password-value" not in str(exc.value)


def test_the_token_is_never_in_the_repr_or_anything_printed(capsys):
    p = person(Aws())
    p.headers()
    assert "id-token" not in repr(p)
    out = capsys.readouterr()
    assert "id-token" not in out.out + out.err


def test_the_module_function_uses_one_shared_cache(monkeypatch):
    aws = Aws()
    monkeypatch.setattr(automation, "_default", person(aws))
    automation.automation_headers(), automation.automation_headers()
    assert aws.minted == 1
