"""The hosted dashboard: its own distribution, an upload only after the build is checked, and the
burn:dashboard_hosted flag, which changes nothing in BurnPeople and BurnApi while it is off."""
import json
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from burn_infra.burn_api import BurnApiStack  # noqa: E402
from burn_infra.dashboard import BurnDashboardStack  # noqa: E402
from burn_infra.people import BurnPeopleStack  # noqa: E402
from burn_contracts import names  # noqa: E402  (path added by burn_infra)

ENV = cdk.Environment(account="123456789012", region="us-east-1")
LOCAL_ORIGIN = "http://localhost:5173"
API_ORIGINS = "https://*.execute-api.us-east-1.amazonaws.com"
COGNITO_ORIGINS = "https://*.auth.us-east-1.amazoncognito.com"


def template(stack_cls, name: str, context: dict | None = None, **kwargs) -> Template:
    return Template.from_stack(stack_cls(cdk.App(context=context or {}), name, env=ENV, **kwargs))


def dashboard_template(dist: Path, context: dict | None = None) -> Template:
    return template(BurnDashboardStack, "BurnDashboard", context, dashboard_dir=dist)


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory) -> Path:
    dist = tmp_path_factory.mktemp("dist")
    (dist / "index.html").write_text('<!doctype html><script type="module" src="/assets/index-7c1e2f.js"></script>')
    (dist / "assets").mkdir()
    (dist / "assets" / "index-7c1e2f.js").write_text("console.log('dashboard')")
    return dist


@pytest.fixture(scope="module")
def dashboard(built_dist):
    return dashboard_template(built_dist)


def ssm_param_ref(template: Template, name: str) -> dict:
    """{"Ref": <CloudFormation parameter>} for an SSM parameter the stack reads when it deploys."""
    for logical_id, param in template.to_json().get("Parameters", {}).items():
        if param.get("Default") == name:
            assert param["Type"] == "AWS::SSM::Parameter::Value<String>"  # resolved at deploy, not at synth
            return {"Ref": logical_id}
    raise AssertionError(f"the stack does not read {name}")


def public_client(template: Template) -> dict:
    return next(c["Properties"] for c in template.find_resources("AWS::Cognito::UserPoolClient").values()
                if c["Properties"]["ClientName"] == "burn-people")


def api_lambda_env(template: Template) -> dict:
    fn = next(iter(template.find_resources("AWS::Lambda::Function",
                                           {"Properties": {"FunctionName": names.BURN_API_FUNCTION}}).values()))
    return fn["Properties"].get("Environment", {}).get("Variables", {})


def cors_origins(template: Template) -> list:
    api = next(iter(template.find_resources("AWS::ApiGatewayV2::Api").values()))
    return api["Properties"]["CorsConfiguration"]["AllowOrigins"]


def csp_text(template: Template) -> str:
    """The dashboard's CSP. Each value CloudFormation reads from SSM at deploy shows as <parameter name>."""
    policy = next(iter(template.find_resources("AWS::CloudFront::ResponseHeadersPolicy").values()))
    csp = policy["Properties"]["ResponseHeadersPolicyConfig"]["SecurityHeadersConfig"]["ContentSecurityPolicy"]
    value = csp["ContentSecurityPolicy"]
    if isinstance(value, str):
        return value
    separator, parts = value["Fn::Join"]
    assert separator == ""
    params = template.to_json()["Parameters"]
    rendered = []
    for part in parts:
        if isinstance(part, str):
            rendered.append(part)
        else:
            param = params[part["Ref"]]
            assert param["Type"] == "AWS::SSM::Parameter::Value<String>"  # resolved at deploy, not at synth
            rendered.append(f"<{param['Default']}>")
    return "".join(rendered)


def csp_directives(template: Template) -> dict[str, list[str]]:
    directives = [d.split() for d in csp_text(template).split(";")]
    return {d[0]: d[1:] for d in directives if d}


# --- the dashboard's own distribution -----------------------------------------------------------

def test_the_dashboard_is_served_at_the_root_of_its_own_distribution(dashboard):
    dashboard.resource_count_is("AWS::CloudFront::Distribution", 1)
    dashboard.resource_count_is("AWS::CloudFront::OriginAccessControl", 1)
    config = next(iter(dashboard.find_resources("AWS::CloudFront::Distribution").values()))[
        "Properties"]["DistributionConfig"]
    assert config["DefaultRootObject"] == "index.html"
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert "CacheBehaviors" not in config  # one behavior: everything, from the root
    assert {r["ErrorCode"]: (r["ResponseCode"], r["ResponsePagePath"]) for r in config["CustomErrorResponses"]} == {
        403: (200, "/index.html"), 404: (200, "/index.html")}  # e.g. the sign-in return to /?code=...
    dashboard.has_resource_properties("AWS::S3::Bucket", {"PublicAccessBlockConfiguration": {
        "BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True}})


def test_the_csp_lets_the_dashboard_call_the_api_and_the_sign_in_domain(dashboard):
    csp = csp_directives(dashboard)
    assert csp["connect-src"] == ["'self'", API_ORIGINS, COGNITO_ORIGINS]
    assert csp["script-src"] == ["'self'"]
    assert csp["default-src"] == ["'self'"]
    assert csp["frame-ancestors"] == ["'none'"]
    assert csp["form-action"] == ["'self'"]  # the dashboard's forms submit through JavaScript
    # Sign-in and sign-out are top-level navigations to the managed login pages: nothing may restrict them.
    assert "navigate-to" not in csp


FLAG_OFF_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "font-src 'self' data:; connect-src 'self' https://*.execute-api.us-east-1.amazonaws.com "
                "https://*.auth.us-east-1.amazoncognito.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def test_before_hosting_the_csp_is_exactly_as_it_was_and_reads_nothing(dashboard):
    assert csp_text(dashboard) == FLAG_OFF_CSP
    params = dashboard.to_json().get("Parameters", {}).values()
    assert not [p for p in params if str(p.get("Default", "")).startswith("/burn/")]  # reads no other stack's values


def test_once_hosted_connect_src_names_exactly_the_api_and_the_sign_in_domain(built_dist):
    hosted = dashboard_template(built_dist, {"burn:dashboard_hosted": "on"})
    csp = csp_directives(hosted)
    assert csp["connect-src"] == ["'self'", f"<{names.API_URL_PARAM}>", f"<{names.PEOPLE_DOMAIN_PARAM}>"]
    assert "*" not in csp_text(hosted)
    before = csp_directives(dashboard_template(built_dist))
    assert {k: v for k, v in csp.items() if k != "connect-src"} == {
        k: v for k, v in before.items() if k != "connect-src"}  # every other directive unchanged


def test_the_address_is_published_to_ssm_and_as_an_output(dashboard):
    dist_id = next(iter(dashboard.find_resources("AWS::CloudFront::Distribution")))
    url = {"Fn::Join": ["", ["https://", {"Fn::GetAtt": [dist_id, "DomainName"]}]]}
    dashboard.has_resource_properties("AWS::SSM::Parameter", {"Name": names.DASHBOARD_URL_PARAM, "Value": url})
    dashboard.has_output("Url", {"Value": url})


# --- uploads happen only through the checked path ------------------------------------------------

@pytest.mark.parametrize("context", [{}, {"burn:dashboard_build": "unchecked"}, {"burn:dashboard_hosted": "on"}])
def test_a_build_is_never_uploaded_unless_it_was_checked(built_dist, context):
    # e.g. make deploy right after make test-dashboard, whose build may hold the demo token
    found = dashboard_template(built_dist, context)
    assert found.find_resources("Custom::CDKBucketDeployment") == {}
    found.resource_count_is("AWS::CloudFront::Distribution", 1)


def test_a_checked_build_is_uploaded_like_the_website(built_dist):
    found = dashboard_template(built_dist, {"burn:dashboard_build": "checked"})
    deployments = found.find_resources("Custom::CDKBucketDeployment").values()
    assert sorted(d["Properties"]["SystemMetadata"]["cache-control"] for d in deployments) == [
        "no-cache", "public, max-age=31536000, immutable"]
    assert all(d["Properties"]["RetainOnDelete"] is True for d in deployments)


def test_a_checked_deploy_without_a_build_uploads_nothing(tmp_path):
    found = dashboard_template(tmp_path / "dist", {"burn:dashboard_build": "checked"})
    assert found.find_resources("Custom::CDKBucketDeployment") == {}


# --- burn:dashboard_hosted off: BurnPeople and BurnApi exactly as before -------------------------

@pytest.mark.parametrize("people_auth", [None, "on"])
@pytest.mark.parametrize("stack_cls,name", [(BurnPeopleStack, "BurnPeople"), (BurnApiStack, "BurnApi")])
def test_flag_off_changes_nothing(stack_cls, name, people_auth):
    base = {"burn:people_auth": people_auth} if people_auth else {}
    without = template(stack_cls, name, base).to_json()
    for off in ("off", "", "true"):  # only "on" switches it on
        assert template(stack_cls, name, {**base, "burn:dashboard_hosted": off}).to_json() == without
    text = json.dumps(without)
    assert names.DASHBOARD_URL_PARAM not in text and names.DASHBOARD_ORIGIN_ENV not in text


def test_flag_off_keeps_the_local_redirects_and_origin():
    client = public_client(template(BurnPeopleStack, "BurnPeople"))
    assert client["CallbackURLs"] == client["LogoutURLs"] == [names.CLI_REDIRECT_URI, names.DASHBOARD_REDIRECT_URI]
    api = template(BurnApiStack, "BurnApi")
    assert cors_origins(api) == [LOCAL_ORIGIN]
    assert names.DASHBOARD_ORIGIN_ENV not in api_lambda_env(api)


# --- burn:dashboard_hosted on ------------------------------------------------------------------

def test_flag_on_adds_the_hosted_root_to_the_sign_in_redirects():
    people = template(BurnPeopleStack, "BurnPeople", {"burn:dashboard_hosted": "on"})
    hosted_root = {"Fn::Join": ["", [ssm_param_ref(people, names.DASHBOARD_URL_PARAM), "/"]]}
    client = public_client(people)
    expected = [names.CLI_REDIRECT_URI, names.DASHBOARD_REDIRECT_URI, hosted_root]
    assert client["CallbackURLs"] == expected
    assert client["LogoutURLs"] == expected


@pytest.mark.parametrize("people_auth", [None, "on"])
def test_flag_on_allows_the_hosted_origin_in_cors(people_auth):
    context = {"burn:dashboard_hosted": "on", **({"burn:people_auth": people_auth} if people_auth else {})}
    api = template(BurnApiStack, "BurnApi", context)
    hosted_origin = ssm_param_ref(api, names.DASHBOARD_URL_PARAM)  # no trailing slash: an origin
    assert cors_origins(api) == [LOCAL_ORIGIN, hosted_origin]
    assert api_lambda_env(api)[names.DASHBOARD_ORIGIN_ENV] == hosted_origin
