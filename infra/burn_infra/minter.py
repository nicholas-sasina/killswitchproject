"""BurnMinter stack: credential minter Lambda and the mock API provider.

The minter can create IAM users, which is the riskiest thing in Burn, so:
- every user it creates must carry the `burn-agent-boundary` permissions
  boundary (enforced by the iam:PermissionsBoundary condition below), so no
  agent key can ever do more than the boundary allows;
- every IAM action is limited to users under the /burn-agents/ path;
- there are no wildcard actions anywhere.
"""
import json
import sys

import aws_cdk as cdk
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from burn_infra.assets import REPO, lambda_asset

# Fixed resource names live in the contracts package; infra has no dependency on it, so add its path.
sys.path.insert(0, str(REPO / "contracts" / "python"))
from burn_contracts import names  # noqa: E402

BEDROCK_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
BOUNDARY_NAME = "burn-agent-boundary"


class BurnMinterStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        acct, region = self.account, self.region
        agent_policy = self._agent_policy_document(acct, region)
        boundary = iam.ManagedPolicy(
            self, "AgentBoundary", managed_policy_name=BOUNDARY_NAME,
            description="Upper limit on what any Burn agent AWS key can do",
            document=iam.PolicyDocument.from_json(agent_policy),
        )
        provider_api = self._provider(acct, region)
        self._minter(acct, region, boundary, agent_policy, provider_api)

    @staticmethod
    def _agent_policy_document(acct: str, region: str) -> dict:
        """Used both as the boundary and as each agent user's inline policy."""
        return {
            "Version": "2012-10-17",
            "Statement": [
                {   # The cross-region inference profile routes to several regions, hence the
                    # foundation-model ARN with a wildcard region (and no account).
                    "Effect": "Allow",
                    "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    "Resource": [
                        f"arn:aws:bedrock:{region}:{acct}:inference-profile/us.{BEDROCK_MODEL}",
                        f"arn:aws:bedrock:*::foundation-model/{BEDROCK_MODEL}",
                    ],
                },
                {   # No resource-level permissions exist for this action.
                    "Effect": "Allow", "Action": "s3:ListAllMyBuckets", "Resource": "*",
                },
            ],
        }

    def _table_arns(self, *names: str) -> list[str]:
        base = f"arn:aws:dynamodb:{self.region}:{self.account}:table"
        return [arn for n in names for arn in (f"{base}/{n}", f"{base}/{n}/index/*")]

    def _role(self, name: str) -> iam.Role:
        return iam.Role(
            self, name, assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole")],
        )

    def _fingerprint_key_arn(self) -> str:
        # Secrets Manager appends a random suffix to the name, hence the trailing '-*'.
        return f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:burn-system/fingerprint-key-*"

    def _provider(self, acct: str, region: str) -> apigw.HttpApi:
        role = self._role("ProviderRole")
        role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"], resources=self._table_arns("burn-fingerprints")))
        role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"], resources=self._table_arns("burn-events")))
        role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"], resources=[self._fingerprint_key_arn()]))
        role.add_to_policy(iam.PolicyStatement(  # canary counters; shared-credential flag lookups
            actions=["dynamodb:GetItem", "dynamodb:UpdateItem"],
            resources=[f"arn:aws:dynamodb:{region}:{acct}:table/{names.CREDENTIALS_TABLE}"]))
        role.add_to_policy(iam.PolicyStatement(  # hand a canary incident to the Burn API; report to the minter
            # Only the Burn API's canary alias, never the function itself: the provider faces the internet,
            # and the alias accepts nothing but canary incidents (an HTTP-shaped event there is refused).
            actions=["lambda:InvokeFunction"],
            resources=[f"arn:aws:lambda:{region}:{acct}:function:{names.BURN_API_CANARY_TARGET}",
                       f"arn:aws:lambda:{region}:{acct}:function:{names.MINTER_FUNCTION}"]))
        fn = lambda_.Function(
            self, "ProviderFn", function_name="burn-provider",
            runtime=lambda_.Runtime.PYTHON_3_12, handler="provider_app.handler",
            code=lambda_.Code.from_asset(lambda_asset("provider", "minter/lambdas/provider")),
            role=role, timeout=cdk.Duration.seconds(10), memory_size=256,
            environment={"BURN_API_FUNCTION": names.BURN_API_CANARY_TARGET},  # read by provider dispatch.py
        )
        api = apigw.HttpApi(self, "ProviderApi", api_name="burn-provider")
        api.add_routes(path="/v1/whoami", methods=[apigw.HttpMethod.GET],
                       integration=integrations.HttpLambdaIntegration("Whoami", fn))
        ssm.StringParameter(self, "ProviderUrl", parameter_name="/burn/provider/url",
                            string_value=api.api_endpoint)
        return api

    def _minter(self, acct, region, boundary, agent_policy, provider_api) -> None:
        role = self._role("MinterRole")
        agent_users = f"arn:aws:iam::{acct}:user/burn-agents/*"
        # Creating a user, or attaching its policy, is allowed ONLY with the boundary set.
        role.add_to_policy(iam.PolicyStatement(
            actions=["iam:CreateUser", "iam:PutUserPolicy"], resources=[agent_users],
            conditions={"StringEquals": {"iam:PermissionsBoundary": boundary.managed_policy_arn}},
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["iam:CreateAccessKey", "iam:UpdateAccessKey", "iam:DeleteAccessKey",
                     "iam:ListAccessKeys", "iam:DeleteUserPolicy", "iam:DeleteUser"],
            resources=[agent_users],
        ))
        # Belt and braces: the boundary can never be removed or swapped.
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.DENY,
            actions=["iam:DeleteUserPermissionsBoundary", "iam:PutUserPermissionsBoundary"],
            resources=["*"],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue",
                     "secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret",
                     "secretsmanager:DeleteSecret", "secretsmanager:RestoreSecret"],
            resources=[f"arn:aws:secretsmanager:{region}:{acct}:secret:burn/*"],
        ))
        role.add_to_policy(iam.PolicyStatement(  # read-only: the HMAC key for fingerprints
            actions=["secretsmanager:GetSecretValue"], resources=[self._fingerprint_key_arn()]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
                     "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:BatchWriteItem"],
            resources=self._table_arns("burn-credentials", "burn-fingerprints"),
        ))
        role.add_to_policy(iam.PolicyStatement(  # GET /flags lists flagged credentials
            actions=["dynamodb:Scan"],
            resources=[f"arn:aws:dynamodb:{region}:{acct}:table/{names.CREDENTIALS_TABLE}"]))
        role.add_to_policy(iam.PolicyStatement(  # CloudTrail LookupEvents has no resource-level
            actions=["cloudtrail:LookupEvents"], resources=["*"],  # permissions: "*" is unavoidable.
        ))
        lambda_.Function(
            self, "MinterFn", function_name=names.MINTER_FUNCTION,
            runtime=lambda_.Runtime.PYTHON_3_12, handler="minter_app.handler",
            code=lambda_.Code.from_asset(lambda_asset("minter", "minter/lambdas/minter")),
            role=role, timeout=cdk.Duration.seconds(60), memory_size=256,
            environment={
                "BOUNDARY_POLICY_ARN": boundary.managed_policy_arn,
                "AGENT_POLICY_JSON": json.dumps(agent_policy),
                "PROVIDER_HOST": f"{provider_api.api_id}.execute-api.{region}.amazonaws.com",
            },
        )
