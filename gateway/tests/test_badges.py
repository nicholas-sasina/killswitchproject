"""Badge helper against moto Cognito and SSM."""
import boto3
import pytest

import badges


@pytest.fixture
def pool(aws):
    cognito = boto3.client("cognito-idp", region_name="us-east-1")
    pool_id = cognito.create_user_pool(PoolName="burn-badges")["UserPool"]["Id"]
    cognito.create_resource_server(UserPoolId=pool_id, Identifier="burn", Name="burn",
                                   Scopes=[{"ScopeName": "tools", "ScopeDescription": "tools"}])
    ssm = boto3.client("ssm", region_name="us-east-1")
    for name, value in {"user-pool-id": pool_id, "scope": "burn/tools",
                        "token-url": "https://example.invalid/oauth2/token"}.items():
        ssm.put_parameter(Name=f"/burn/identity/{name}", Value=value, Type="String")
    return pool_id


def test_create_badge_is_a_client_credentials_client(pool):
    badge = badges.create_badge("agent-alpha", "s-123")

    assert set(badge) == {"client_id", "client_secret", "token_url", "scope"}
    assert badge["scope"] == "burn/tools" and badge["client_secret"]
    client = boto3.client("cognito-idp", region_name="us-east-1").describe_user_pool_client(
        UserPoolId=pool, ClientId=badge["client_id"])["UserPoolClient"]
    assert client["AllowedOAuthFlows"] == ["client_credentials"]
    assert client["AllowedOAuthScopes"] == ["burn/tools"]
    assert client["ClientName"] == "burn-agent-alpha-s-123"


def test_delete_badge_is_idempotent(pool):
    badge = badges.create_badge("agent-alpha", "s-1")
    assert badges.delete_badge(badge["client_id"]) is True
    assert badges.delete_badge(badge["client_id"]) is False


def test_client_name_keeps_only_allowed_characters():
    assert badges.badge_client_name("agent-a", "s/1:2") == "burn-agent-a-s-1-2"
    assert len(badges.badge_client_name("agent-a", "x" * 300)) == 128


def test_missing_identity_stack_is_a_clear_error(aws):
    with pytest.raises(RuntimeError, match="BurnIdentity"):
        badges.create_badge("agent-alpha", "s-1")
