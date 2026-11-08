"""People sign-in: the people pool and the API's JWT authorizer, with the flag off and on."""
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from burn_infra.burn_api import BurnApiStack  # noqa: E402
from burn_infra.identity import BurnIdentityStack  # noqa: E402
from burn_infra.people import BurnPeopleStack  # noqa: E402
from burn_contracts import names  # noqa: E402  (path added by burn_infra)

ENV = cdk.Environment(account="123456789012", region="us-east-1")
INSTANCE_ROUTE_KEYS = {"POST /instances", "POST /instances/{session_id}/heartbeat",
                       "POST /instances/{session_id}/exit"}


def api_template(flag: str | None) -> Template:
    app = cdk.App(context={"burn:people_auth": flag} if flag else {})
    return Template.from_stack(BurnApiStack(app, "BurnApi", env=ENV))


@pytest.fixture(scope="module")
def people():
    return Template.from_stack(BurnPeopleStack(cdk.App(), "BurnPeople", env=ENV))


@pytest.fixture(scope="module")
def api_on():
    return api_template("on")


@pytest.fixture(scope="module")
def api_off():
    return api_template(None)


def routes(template: Template) -> dict[str, dict]:
    return {r["Properties"]["RouteKey"]: r["Properties"]
            for r in template.find_resources("AWS::ApiGatewayV2::Route").values()}


def lambda_env(template: Template) -> dict:
    fn = next(iter(template.find_resources("AWS::Lambda::Function",
                                           {"Properties": {"FunctionName": names.BURN_API_FUNCTION}}).values()))
    return fn["Properties"].get("Environment", {}).get("Variables", {})


def ssm_value(template: Template, name: str):
    for param in template.find_resources("AWS::SSM::Parameter").values():
        if param["Properties"]["Name"] == name:
            return param["Properties"]["Value"]
    raise AssertionError(f"no SSM parameter {name}")


# --- the people pool ---------------------------------------------------------------------------

def test_the_pool_is_invite_only_and_signs_in_by_email(people):
    people.resource_count_is("AWS::Cognito::UserPool", 1)
    people.has_resource_properties("AWS::Cognito::UserPool", {
        "UserPoolName": names.PEOPLE_POOL_NAME,
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},  # self sign-up off
        "UsernameAttributes": ["email"],
    })


def test_the_managed_login_domain_embeds_the_account_id(people):
    domain = next(iter(people.find_resources("AWS::Cognito::UserPoolDomain").values()))["Properties"]["Domain"]
    assert domain == {"Fn::Join": ["", ["burn-people-", {"Ref": "AWS::AccountId"}]]}


def test_the_public_client_is_code_grant_only_with_every_scope_and_both_redirects(people):
    people.has_resource_properties("AWS::Cognito::UserPoolClient", {
        "ClientName": "burn-people",
        "GenerateSecret": False,
        "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH"],  # no password flow of any kind
        "AllowedOAuthFlows": ["code"],
        "AllowedOAuthFlowsUserPoolClient": True,
        "AllowedOAuthScopes": Match.array_with(["openid", "email", "profile"]),
        "CallbackURLs": [names.CLI_REDIRECT_URI, names.DASHBOARD_REDIRECT_URI],
        "LogoutURLs": [names.CLI_REDIRECT_URI, names.DASHBOARD_REDIRECT_URI],
        "SupportedIdentityProviders": ["COGNITO"],
        "IdTokenValidity": 60, "RefreshTokenValidity": 43200,
        "TokenValidityUnits": {"IdToken": "minutes", "RefreshToken": "minutes", "AccessToken": "minutes"},
    })
    client = next(c["Properties"] for c in people.find_resources("AWS::Cognito::UserPoolClient").values()
                  if c["Properties"]["ClientName"] == "burn-people")
    assert sorted(client["AllowedOAuthScopes"]) == ["email", "openid", "profile"]


def test_the_automation_client_has_a_secret_and_only_the_admin_password_flow(people):
    client = next(c["Properties"] for c in people.find_resources("AWS::Cognito::UserPoolClient").values()
                  if c["Properties"]["ClientName"] == names.AUTOMATION_CLIENT_NAME)
    assert client["GenerateSecret"] is True
    assert sorted(client["ExplicitAuthFlows"]) == ["ALLOW_ADMIN_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
    assert not client.get("AllowedOAuthFlowsUserPoolClient") and "AllowedOAuthFlows" not in client


def test_the_people_values_are_published_to_ssm(people):
    for name in (names.PEOPLE_USER_POOL_ID_PARAM, names.PEOPLE_CLIENT_ID_PARAM, names.AUTOMATION_CLIENT_ID_PARAM,
                 names.PEOPLE_DOMAIN_PARAM, names.PEOPLE_ISSUER_PARAM):
        ssm_value(people, name)
    issuer = ssm_value(people, names.PEOPLE_ISSUER_PARAM)
    assert "https://cognito-idp.us-east-1.amazonaws.com/" in str(issuer)


def test_the_badges_pool_is_untouched():
    badges = Template.from_stack(BurnIdentityStack(cdk.App(), "BurnIdentity", env=ENV))
    badges.resource_count_is("AWS::Cognito::UserPool", 1)
    badges.has_resource_properties("AWS::Cognito::UserPool", {"UserPoolName": "burn-badges"})
    badges.resource_count_is("AWS::Cognito::UserPoolClient", 0)


# --- the API with the flag off: nothing changes for callers ------------------------------------

def test_flag_off_has_no_authorizer_and_one_open_default_route(api_off):
    api_off.resource_count_is("AWS::ApiGatewayV2::Authorizer", 0)
    assert set(routes(api_off)) == {"$default"}
    assert routes(api_off)["$default"].get("AuthorizationType", "NONE") == "NONE"
    assert names.PEOPLE_AUTH_ENV not in lambda_env(api_off)
    assert ssm_value(api_off, names.PEOPLE_ENABLED_PARAM) == "off"


# --- the API with the flag on ------------------------------------------------------------------

def test_flag_on_puts_the_jwt_authorizer_on_every_route_but_the_instance_routes(api_on):
    api_on.resource_count_is("AWS::ApiGatewayV2::Authorizer", 1)
    api_on.has_resource_properties("AWS::ApiGatewayV2::Authorizer", {
        "AuthorizerType": "JWT", "IdentitySource": ["$request.header.Authorization"]})
    found = routes(api_on)
    assert found["$default"]["AuthorizationType"] == "JWT"
    for key in INSTANCE_ROUTE_KEYS | {"OPTIONS /{proxy+}"}:
        assert found[key]["AuthorizationType"] == "NONE", key
        assert "AuthorizerId" not in found[key]
    assert set(found) == {"$default", "OPTIONS /{proxy+}"} | INSTANCE_ROUTE_KEYS


def test_flag_on_trusts_the_people_pool_and_both_clients(api_on):
    jwt = next(iter(api_on.find_resources("AWS::ApiGatewayV2::Authorizer").values()))["Properties"]["JwtConfiguration"]
    params = api_on.to_json()["Parameters"]
    def param_name(ref):
        return params[ref["Ref"]]["Default"]
    assert param_name(jwt["Issuer"]) == names.PEOPLE_ISSUER_PARAM
    assert sorted(param_name(a) for a in jwt["Audience"]) == sorted(
        [names.PEOPLE_CLIENT_ID_PARAM, names.AUTOMATION_CLIENT_ID_PARAM])


def test_flag_on_switches_the_lambda_and_says_so(api_on):
    assert lambda_env(api_on)[names.PEOPLE_AUTH_ENV] == "1"
    assert names.LOCAL_DEV_ENV not in lambda_env(api_on)  # a deployment never opens the admin header
    assert ssm_value(api_on, names.PEOPLE_ENABLED_PARAM) == "on"


@pytest.mark.parametrize("flag", ["on", None])
def test_cors_allows_the_authorization_header(flag):
    template = api_template(flag)
    template.has_resource_properties("AWS::ApiGatewayV2::Api", {
        "CorsConfiguration": Match.object_like({"AllowHeaders": Match.array_with(["authorization"])})})
