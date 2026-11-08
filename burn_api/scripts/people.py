#!/usr/bin/env python3
"""Invite people to the people user pool, and set up the automation person.

    python scripts/people.py invite ada@example.com   # Cognito emails an invite with a temporary password
    python scripts/people.py automation               # create or refresh the automation person

The automation person is how scripts that run without a person (e2e, smoke) sign in: AdminInitiateAuth
through the burn-automation app client. Its password is generated here and, together with the username and
the client's secret, written to Secrets Manager (names.AUTOMATION_PERSON_SECRET) as JSON. Nothing secret is
ever printed. Running it again sets a new password, so it also rotates.
"""
import json
import secrets
import sys

import boto3
from burn_contracts import names

AUTOMATION_EMAIL = "automation@burn.invalid"  # never receives mail: the invite is suppressed


def _param(ssm, name: str) -> str:
    return ssm.get_parameter(Name=name)["Parameter"]["Value"]


def new_password() -> str:
    # 43 url-safe chars from 32 random bytes, plus one of each class Cognito's default policy asks for.
    return secrets.token_urlsafe(32) + "aA1!"


def invite(cognito, pool_id: str, email: str) -> None:
    cognito.admin_create_user(
        UserPoolId=pool_id, Username=email, DesiredDeliveryMediums=["EMAIL"],
        UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}])


def automation(cognito, secretsmanager, pool_id: str, client_id: str, email: str = AUTOMATION_EMAIL) -> str:
    """Create the automation person if needed, give it a fresh password and store its sign-in secret."""
    try:
        cognito.admin_create_user(
            UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}])
    except cognito.exceptions.UsernameExistsException:
        pass  # a rerun: keep the person, set a new password below
    password = new_password()
    cognito.admin_set_user_password(UserPoolId=pool_id, Username=email, Password=password, Permanent=True)
    client_secret = cognito.describe_user_pool_client(
        UserPoolId=pool_id, ClientId=client_id)["UserPoolClient"]["ClientSecret"]
    value = json.dumps({"username": email, "password": password, "client_secret": client_secret})
    try:
        secretsmanager.put_secret_value(SecretId=names.AUTOMATION_PERSON_SECRET, SecretString=value)
    except secretsmanager.exceptions.ResourceNotFoundException:
        secretsmanager.create_secret(Name=names.AUTOMATION_PERSON_SECRET, SecretString=value,
                                     Description="Sign-in for scripts that run without a person")
    return email


def main(argv: list[str]) -> int:
    if len(argv) < 1 or argv[0] not in ("invite", "automation") or (argv[0] == "invite" and len(argv) != 2):
        print(__doc__.strip().splitlines()[0])
        print("usage: people.py invite EMAIL | people.py automation")
        return 2
    ssm = boto3.client("ssm", region_name=names.REGION)
    cognito = boto3.client("cognito-idp", region_name=names.REGION)
    pool_id = _param(ssm, names.PEOPLE_USER_POOL_ID_PARAM)
    if argv[0] == "invite":
        invite(cognito, pool_id, argv[1])
        print(f"invited {argv[1]}: Cognito emails them a temporary password")
    else:
        email = automation(cognito, boto3.client("secretsmanager", region_name=names.REGION), pool_id,
                           _param(ssm, names.AUTOMATION_CLIENT_ID_PARAM))
        print(f"automation person {email} ready; its sign-in is in {names.AUTOMATION_PERSON_SECRET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
