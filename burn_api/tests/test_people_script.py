"""scripts/people.py: invites, and the automation person whose sign-in is never printed."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import people  # noqa: E402
from burn_contracts import names  # noqa: E402


class FakeCognito:
    class exceptions:
        class UsernameExistsException(Exception):
            pass

    def __init__(self):
        self.users, self.passwords, self.invites = {}, {}, []

    def admin_create_user(self, UserPoolId, Username, UserAttributes, MessageAction=None,
                          DesiredDeliveryMediums=None):
        if Username in self.users:
            raise self.exceptions.UsernameExistsException()
        self.users[Username] = {a["Name"]: a["Value"] for a in UserAttributes}
        if MessageAction != "SUPPRESS":
            self.invites.append((Username, DesiredDeliveryMediums))

    def admin_set_user_password(self, UserPoolId, Username, Password, Permanent):
        assert Permanent is True and Username in self.users
        self.passwords[Username] = Password

    def describe_user_pool_client(self, UserPoolId, ClientId):
        return {"UserPoolClient": {"ClientId": ClientId, "ClientSecret": "client-secret-" + ClientId}}


class FakeSecrets:
    class exceptions:
        class ResourceNotFoundException(Exception):
            pass

    def __init__(self):
        self.values = {}

    def put_secret_value(self, SecretId, SecretString):
        if SecretId not in self.values:
            raise self.exceptions.ResourceNotFoundException()
        self.values[SecretId] = SecretString

    def create_secret(self, Name, SecretString, Description):
        self.values[Name] = SecretString


def test_invite_sends_cognitos_email_invite():
    cognito = FakeCognito()
    people.invite(cognito, "pool", "ada@example.com")
    assert cognito.invites == [("ada@example.com", ["EMAIL"])]
    assert cognito.users["ada@example.com"] == {"email": "ada@example.com", "email_verified": "true"}


def test_automation_stores_everything_a_script_needs_in_one_secret():
    cognito, store = FakeCognito(), FakeSecrets()
    email = people.automation(cognito, store, "pool", "automation-client")
    stored = json.loads(store.values[names.AUTOMATION_PERSON_SECRET])
    assert stored == {"username": email, "password": cognito.passwords[email],
                      "client_secret": "client-secret-automation-client"}
    assert cognito.invites == []  # no email for the automation person


def test_a_rerun_keeps_the_person_and_rotates_the_password():
    cognito, store = FakeCognito(), FakeSecrets()
    people.automation(cognito, store, "pool", "c")
    first = json.loads(store.values[names.AUTOMATION_PERSON_SECRET])["password"]
    people.automation(cognito, store, "pool", "c")
    second = json.loads(store.values[names.AUTOMATION_PERSON_SECRET])["password"]
    assert first != second and len(cognito.users) == 1


def test_passwords_meet_cognitos_default_policy_and_are_long():
    for _ in range(50):
        pw = people.new_password()
        assert len(pw) >= 40
        assert any(c.islower() for c in pw) and any(c.isupper() for c in pw)
        assert any(c.isdigit() for c in pw) and any(not c.isalnum() for c in pw)


def test_nothing_secret_is_printed(monkeypatch, capsys):
    cognito, store = FakeCognito(), FakeSecrets()

    class SSM:
        def get_parameter(self, Name):
            return {"Parameter": {"Value": {"/burn/people/user-pool-id": "pool"}.get(Name, "automation-client")}}

    clients = {"ssm": SSM(), "cognito-idp": cognito, "secretsmanager": store}
    monkeypatch.setattr(people.boto3, "client", lambda service, region_name=None: clients[service])
    assert people.main(["automation"]) == 0
    stored = json.loads(store.values[names.AUTOMATION_PERSON_SECRET])
    out = capsys.readouterr()
    for secret in (stored["password"], stored["client_secret"]):
        assert secret not in out.out and secret not in out.err


@pytest.mark.parametrize("argv", [[], ["invite"], ["nope"], ["invite", "a@b.c", "extra"]])
def test_bad_usage(argv, capsys):
    assert people.main(argv) == 2
