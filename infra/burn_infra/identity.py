"""BurnIdentity stack: Cognito user pool, OAuth scope and domain for agent badges.

The pool holds no users. Each agent session gets its own app client (a badge),
created at runtime by the Burn API with gateway/burn_badges. The AgentCore
Gateway trusts tokens from this pool that carry the burn/tools scope.

Values AWS generates here are published to SSM under /burn/identity/ so the
gateway stack, the Burn API and scripts read them by name.
"""
import aws_cdk as cdk
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class BurnIdentityStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        pool = cognito.UserPool(
            self, "Badges",
            user_pool_name="burn-badges",
            self_sign_up_enabled=False,  # machine clients only, no human sign-ups
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        tools = cognito.ResourceServerScope(
            scope_name="tools", scope_description="Call Burn's MCP tools through the gateway"
        )
        server = pool.add_resource_server("BurnServer", identifier="burn", scopes=[tools])
        full_scope = cognito.OAuthScope.resource_server(server, tools).scope_name  # "burn/tools"

        # The token endpoint needs a domain. The prefix must be globally unique,
        # so it embeds the account id, resolved at deploy time (never in the repo).
        domain = pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"burn-badges-{cdk.Aws.ACCOUNT_ID}"
            ),
        )

        discovery_url = (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{pool.user_pool_id}/.well-known/openid-configuration"
        )
        params = {
            "user-pool-id": pool.user_pool_id,
            "user-pool-arn": pool.user_pool_arn,
            "token-url": domain.base_url() + "/oauth2/token",
            "scope": full_scope,
            "discovery-url": discovery_url,
        }
        for name, value in params.items():
            ssm.StringParameter(
                self, f"Param-{name}",
                parameter_name=f"/burn/identity/{name}",
                string_value=value,
            )
