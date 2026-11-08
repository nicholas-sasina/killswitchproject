"""BurnPeople stack: the invite-only user pool people sign in with.

Separate from the burn-badges pool (machine clients for agent sessions), which it never touches.

- Sign-in by email. No self sign-up: a person is invited (`make person EMAIL=...`).
- A managed login domain whose prefix embeds the account id, like the badges domain.
- A public app client for the CLI and the dashboard: no secret, authorization-code grant
  (the clients use PKCE), scopes openid, email and profile, and the two loopback addresses as
  callback and sign-out URLs, plus the hosted dashboard's root with burn:dashboard_hosted=on.
  It accepts no password flow.
- A confidential app client, burn-automation, for scripts that run without a person (e2e, smoke).
  It accepts only the admin password flow (AdminInitiateAuth, which needs AWS credentials) and
  refresh, and no OAuth flow.

The values other stacks and scripts need are published to SSM under /burn/people/.
"""
import sys

import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from .assets import REPO
from .dashboard import hosted_dashboard_url

# Fixed resource names live in the contracts package; infra has no dependency on it, so add its path.
sys.path.insert(0, str(REPO / "contracts" / "python"))
from burn_contracts import names  # noqa: E402

REDIRECTS = [names.CLI_REDIRECT_URI, names.DASHBOARD_REDIRECT_URI]


class BurnPeopleStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        pool = cognito.UserPool(
            self, "People",
            user_pool_name=names.PEOPLE_POOL_NAME,
            self_sign_up_enabled=False,  # invite-only
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True)),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # The prefix must be globally unique, so it embeds the account id, resolved at deploy time.
        domain = pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"burn-people-{cdk.Aws.ACCOUNT_ID}"),
        )

        redirects = list(REDIRECTS)
        hosted = hosted_dashboard_url(self)  # None unless burn:dashboard_hosted=on
        if hosted:
            redirects.append(f"{hosted}/")  # the hosted dashboard signs in and out at its root
        public = pool.add_client(
            "Public",
            user_pool_client_name="burn-people",
            generate_secret=False,
            # No password flow of any kind; sign-in happens on the managed login page. Refresh is implied.
            auth_flows=cognito.AuthFlow(user_password=False, user_srp=False, admin_user_password=False,
                                        custom=False),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True, implicit_code_grant=False),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                callback_urls=redirects,
                logout_urls=redirects,
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            id_token_validity=cdk.Duration.hours(1),
            access_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.days(30),
            prevent_user_existence_errors=True,
        )

        automation = pool.add_client(
            "Automation",
            user_pool_client_name=names.AUTOMATION_CLIENT_NAME,
            generate_secret=True,
            auth_flows=cognito.AuthFlow(admin_user_password=True),  # plus refresh, which CDK always adds
            disable_o_auth=True,
            id_token_validity=cdk.Duration.hours(1),
            access_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.days(30),
            prevent_user_existence_errors=True,
        )

        params = {
            names.PEOPLE_USER_POOL_ID_PARAM: pool.user_pool_id,
            names.PEOPLE_CLIENT_ID_PARAM: public.user_pool_client_id,
            names.AUTOMATION_CLIENT_ID_PARAM: automation.user_pool_client_id,
            names.PEOPLE_DOMAIN_PARAM: domain.base_url(),  # https origin, no path
            names.PEOPLE_ISSUER_PARAM: f"https://cognito-idp.{self.region}.amazonaws.com/{pool.user_pool_id}",
        }
        for name, value in params.items():
            ssm.StringParameter(self, f"Param{name.rsplit('/', 1)[-1].title().replace('-', '')}",
                                parameter_name=name, string_value=value)
