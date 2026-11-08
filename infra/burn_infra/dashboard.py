"""BurnDashboard stack: the dashboard, hosted at the root of its own CloudFront distribution.

It reuses StaticSite (see site.py). The dashboard signs people in and out with redirect URIs at
its origin's root (`new URL("/", location.origin)`), so it needs its own distribution rather than
a path under the website's.

A dashboard build made before people sign-in is switched on can carry the admin demo token, which
must never be public. So this stack never uploads dashboard/dist on its own. It uploads only when
deployed with `-c burn:dashboard_build=checked`, which `make dashboard-deploy` passes after it has
checked that sign-in is on, rebuilt the dashboard without the token, and scanned the build for it.
Any other deploy (make deploy, cdk deploy --all, or BurnDashboard pulled in as a dependency) leaves
the files already uploaded as they are.

`-c burn:dashboard_hosted=on` (default off) points the rest of Burn at the hosted dashboard:
BurnPeople adds its address to the sign-in callback and sign-out URLs, and BurnApi allows its origin
in CORS. Both read the address from SSM when they deploy, so BurnDashboard must have been deployed
first. With the flag off, neither stack changes at all.
"""
import sys

import aws_cdk as cdk
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from .assets import REPO
from .site import StaticSite

# Fixed resource names live in the contracts package; infra has no dependency on it, so add its path.
sys.path.insert(0, str(REPO / "contracts" / "python"))
from burn_contracts import names  # noqa: E402

DASHBOARD_DIST = REPO / "dashboard" / "dist"
DASHBOARD_HOSTED_CONTEXT = "burn:dashboard_hosted"  # "on": BurnPeople and BurnApi accept the hosted dashboard
DASHBOARD_BUILD_CONTEXT = "burn:dashboard_build"    # "checked": set only by make dashboard-deploy

# Like the website's CSP, plus the two origins the dashboard calls with fetch: the Burn API and the
# people sign-in domain (for /oauth2/token). Wildcards keep this stack independent of BurnApi and
# BurnPeople; both names exist only in this region. Signing in and out are top-level navigations to
# the managed login pages, which a CSP does not restrict. The dashboard's forms submit through
# JavaScript, so form-action stays 'self'.
DASHBOARD_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    f"connect-src 'self' https://*.execute-api.{names.REGION}.amazonaws.com"
    f" https://*.auth.{names.REGION}.amazoncognito.com",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


def hosted_dashboard_url(scope: Construct) -> str | None:
    """The hosted dashboard's address (https://..., no trailing slash) with burn:dashboard_hosted=on, else None.

    CloudFormation reads it from SSM when the stack deploys, not at synth, so nothing account-specific
    is looked up or cached here. The parameter must exist by then: BurnDashboard writes it.
    """
    if scope.node.try_get_context(DASHBOARD_HOSTED_CONTEXT) != "on":
        return None
    return ssm.StringParameter.value_for_string_parameter(scope, names.DASHBOARD_URL_PARAM)


class BurnDashboardStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, dashboard_dir=DASHBOARD_DIST, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        checked = self.node.try_get_context(DASHBOARD_BUILD_CONTEXT) == "checked"
        site = StaticSite(self, "Dashboard", name="Burn dashboard", source_dir=dashboard_dir if checked else None,
                          content_security_policy=DASHBOARD_CSP)
        if not checked:
            cdk.Annotations.of(self).add_info(
                "nothing is uploaded: only make dashboard-deploy uploads the dashboard, after checking the build")

        ssm.StringParameter(self, "UrlParam", parameter_name=names.DASHBOARD_URL_PARAM, string_value=site.url)
        cdk.CfnOutput(self, "Url", value=site.url, description="The hosted dashboard's address")
