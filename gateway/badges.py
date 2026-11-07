"""Badges: one Cognito app client per agent session.

A badge is an OAuth client (client_credentials grant, scope burn/tools).
The agent trades it for a short-lived access token at the token URL and
sends that token to the AgentCore Gateway, which checks the signature and
scope. One client per session means every token traces to exactly one
agent session, and a burned session's client can be deleted without
touching any other agent.

Deleting the client stops NEW tokens only: tokens already issued stay
valid until they expire. The real kill switch is the session status in
DynamoDB, checked by the gateway interceptor on every call.

A single stdlib + boto3 file, so the Burn API Lambda can bundle it as-is
(its role needs cognito-idp Create/DeleteUserPoolClient and
ssm:GetParameter on /burn/identity/*).
"""
import functools
import re

import boto3

REGION = "us-east-1"
POOL_ID_PARAM = "/burn/identity/user-pool-id"
TOKEN_URL_PARAM = "/burn/identity/token-url"
SCOPE_PARAM = "/burn/identity/scope"

# Cognito allows [\w\s+=,.@-] in client names, up to 128 chars.
_NAME_UNSAFE = re.compile(r"[^\w\s+=,.@-]")


@functools.cache
def _cognito():
    return boto3.client("cognito-idp", region_name=REGION)


@functools.cache
def identity_config() -> dict:
    """User pool id, token URL and scope, published by the BurnIdentity stack."""
    ssm = boto3.client("ssm", region_name=REGION)

    def param(name: str) -> str:
        try:
            return ssm.get_parameter(Name=name)["Parameter"]["Value"]
        except ssm.exceptions.ParameterNotFound:
            raise RuntimeError(f"SSM parameter {name} missing (deploy BurnIdentity first)") from None

    return {
        "user_pool_id": param(POOL_ID_PARAM),
        "token_url": param(TOKEN_URL_PARAM),
        "scope": param(SCOPE_PARAM),
    }


def badge_client_name(agent_id: str, session_id: str) -> str:
    """Readable client name so the Cognito console shows whose badge it is."""
    return _NAME_UNSAFE.sub("-", f"burn-{agent_id}-{session_id}")[:128]


def create_badge(agent_id: str, session_id: str) -> dict:
    """Create the session's app client. Returns {client_id, client_secret, token_url, scope}.

    The secret is only returned here. Keep it (the Burn API stores it in
    Secrets Manager) instead of reading it back later: DescribeUserPoolClient
    is limited to a few calls per second per pool.
    """
    config = identity_config()
    client = _cognito().create_user_pool_client(
        UserPoolId=config["user_pool_id"],
        ClientName=badge_client_name(agent_id, session_id),
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthFlowsUserPoolClient=True,
        AllowedOAuthScopes=[config["scope"]],
        SupportedIdentityProviders=["COGNITO"],
        AccessTokenValidity=60,
        TokenValidityUnits={"AccessToken": "minutes"},
    )["UserPoolClient"]
    return {
        "client_id": client["ClientId"],
        "client_secret": client["ClientSecret"],
        "token_url": config["token_url"],
        "scope": config["scope"],
    }


def delete_badge(client_id: str) -> bool:
    """Delete the app client so it can mint no new tokens. False if it was already gone."""
    try:
        _cognito().delete_user_pool_client(
            UserPoolId=identity_config()["user_pool_id"], ClientId=client_id
        )
    except _cognito().exceptions.ResourceNotFoundException:
        return False
    return True
