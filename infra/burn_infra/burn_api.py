"""BurnApi stack: API Gateway HTTP API in front of one Lambda that serves every Burn API route.

Least privilege: the role can touch only the Burn tables, the demo-token secret,
the per-agent badge secrets, the minter Lambda and Cognito app clients.
"""
import pathlib
import shutil
import sys

import aws_cdk as cdk
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from .assets import REPO, lambda_asset

# Fixed resource names live in the contracts package; infra has no dependency on it, so add its path.
sys.path.insert(0, str(REPO / "contracts" / "python"))
from burn_contracts import names  # noqa: E402

DASHBOARD_ORIGIN = "http://localhost:5173"
BADGE_HELPER = REPO / "gateway" / "badges.py"  # shared with the gateway code that owns Cognito badges


class BurnApiStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        acct, region = self.account, self.region

        def table_arn(name: str) -> str:
            return f"arn:aws:dynamodb:{region}:{acct}:table/{name}"

        code_dir = lambda_asset("burn-api", "burn_api/lambdas/api")
        if BADGE_HELPER.exists():
            shutil.copy(BADGE_HELPER, pathlib.Path(code_dir) / "badges.py")
        else:
            print(f"warning: {BADGE_HELPER} not found; BurnApi would fail on POST /sessions")

        role = iam.Role(
            self, "Role", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole")],
        )
        role.add_to_policy(iam.PolicyStatement(
            # Scan: instance, role, parent and token lookups are filtered scans of this table.
            actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem",
                     "dynamodb:Scan"],
            resources=[table_arn(names.SESSIONS_TABLE), f"{table_arn(names.SESSIONS_TABLE)}/index/by_agent"],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[table_arn(names.SCENARIO_TABLE), table_arn(names.CREDENTIALS_TABLE)],
        ))
        role.add_to_policy(iam.PolicyStatement(  # roles: get, put and list (Scan) only
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"],
            resources=[table_arn(names.ROLES_TABLE)],
        ))
        role.add_to_policy(iam.PolicyStatement(  # join tokens: create, redeem (conditional update), revoke
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
            resources=[table_arn(names.ENROLL_TOKENS_TABLE)],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem", "dynamodb:Query"], resources=[table_arn(names.EVENTS_TABLE)]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"], resources=[table_arn(names.EXPOSURES_TABLE)]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{region}:{acct}:secret:{names.DEMO_TOKEN_SECRET}-*"],
        ))
        role.add_to_policy(iam.PolicyStatement(  # badge secrets: burn/<agent_id>/badge
            actions=["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"],
            resources=[f"arn:aws:secretsmanager:{region}:{acct}:secret:burn/*/badge-*"],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[f"arn:aws:lambda:{region}:{acct}:function:{names.MINTER_FUNCTION}"],
        ))
        # Pool ARN is published by BurnIdentity; resolved by CloudFormation at deploy time.
        pool_arn = ssm.StringParameter.value_for_string_parameter(self, "/burn/identity/user-pool-arn")
        role.add_to_policy(iam.PolicyStatement(  # one Cognito app client per session
            actions=["cognito-idp:CreateUserPoolClient", "cognito-idp:DeleteUserPoolClient",
                     "cognito-idp:DescribeUserPool"],
            resources=[pool_arn],
        ))
        role.add_to_policy(iam.PolicyStatement(  # badge helper finds the pool via SSM
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{region}:{acct}:parameter/burn/identity/*"],
        ))

        fn = lambda_.Function(
            self, "Function", function_name="burn-api", runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler", code=lambda_.Code.from_asset(code_dir), role=role,
            timeout=cdk.Duration.seconds(29), memory_size=256,
        )

        api = apigw.HttpApi(
            self, "Api", api_name="burn-api",
            default_integration=integrations.HttpLambdaIntegration("Integration", fn),
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=[DASHBOARD_ORIGIN],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.PUT,
                               apigw.CorsHttpMethod.DELETE, apigw.CorsHttpMethod.OPTIONS],
                allow_headers=["content-type", names.DEMO_TOKEN_HEADER],
            ),
        )
        ssm.StringParameter(self, "UrlParam", parameter_name="/burn/api/url", string_value=api.api_endpoint)
