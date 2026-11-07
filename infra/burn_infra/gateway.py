"""BurnGateway stack: AgentCore Gateway, request/response interceptor Lambda, mock tool Lambda.

- The gateway speaks MCP and accepts only Cognito tokens with the burn/tools
  scope (BurnIdentity publishes the discovery URL and scope to SSM).
- One Lambda target serves every tool in contracts/mcp-tools.json; the schema
  is loaded from that file at synth time so names cannot drift.
- One interceptor Lambda runs at REQUEST and RESPONSE with request headers,
  so it can read the caller's token. Response streaming stays off, otherwise
  RESPONSE would fire once per streamed chunk.
"""
import json

import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from .assets import REPO, lambda_asset

GATEWAY_NAME = "burn-gateway"
TARGET_NAME = "burn-tools"  # tools appear to agents as "burn-tools___<tool>"
TOOLS_FUNCTION = "burn-gateway-tools"
INTERCEPTOR_FUNCTION = "burn-gateway-interceptor"


def _schema(node: dict) -> agentcore.CfnGatewayTarget.SchemaDefinitionProperty:
    """JSON Schema dict -> the CloudFormation schema type (same field names)."""
    return agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
        type=node["type"],
        description=node.get("description"),
        properties={k: _schema(v) for k, v in node["properties"].items()} if node.get("properties") else None,
        required=node.get("required") or None,
        items=_schema(node["items"]) if "items" in node else None,
    )


def load_tool_definitions() -> list:
    tools = json.loads((REPO / "contracts" / "mcp-tools.json").read_text())["tools"]
    return [
        agentcore.CfnGatewayTarget.ToolDefinitionProperty(
            name=t["name"], description=t["description"], input_schema=_schema(t["inputSchema"])
        )
        for t in tools
    ]


class BurnGatewayStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        def arn(service: str, resource: str) -> str:
            return f"arn:aws:{service}:{self.region}:{self.account}:{resource}"

        def table(name: str) -> str:
            return arn("dynamodb", f"table/{name}")

        def python_fn(cid: str, name: str, folder: str, handler: str,
                      shared: tuple[str, ...] = ("burn_contracts",)) -> lambda_.Function:
            return lambda_.Function(
                self, cid,
                function_name=name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=lambda_.Code.from_asset(lambda_asset(name, folder, shared=shared)),
                timeout=cdk.Duration.seconds(15),
                memory_size=512,
                log_group=logs.LogGroup(
                    self, f"{cid}Logs",
                    log_group_name=f"/aws/lambda/{name}",
                    retention=logs.RetentionDays.ONE_WEEK,
                    removal_policy=cdk.RemovalPolicy.DESTROY,
                ),
            )

        # --- Tool Lambda: scenario data, messages, placeholder values --------
        tools_fn = python_fn("ToolsFn", TOOLS_FUNCTION, "gateway/lambdas/tools", "tools.handler")
        tools_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Scan"], resources=[table("burn-scenario")]))
        tools_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem", "dynamodb:Query", "dynamodb:DeleteItem"],
            resources=[table("burn-messages")]))
        # Only the secrets placeholders may name: agents' api_key/aws_key and
        # shared credentials. Not badges, not burn-system/*. (ARNs end in a random suffix.)
        tools_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[arn("secretsmanager", "secret:burn/agent-*/api_key-*"),
                       arn("secretsmanager", "secret:burn/agent-*/aws_key-*"),
                       arn("secretsmanager", "secret:burn/shared/*")]))

        # --- Interceptor Lambda: badge check, allowlists, ledger, exit check ---
        interceptor_fn = python_fn(
            "InterceptorFn", INTERCEPTOR_FUNCTION, "gateway/lambdas/interceptor", "interceptor.handler",
            shared=("burn_contracts", "burn_detect"))
        interceptor_fn.add_environment("BURN_TOOL_TARGET", TARGET_NAME)
        interceptor_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Query"],
            resources=[table("burn-sessions"), table("burn-sessions") + "/index/by_client"]))
        interceptor_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"], resources=[table("burn-events")]))
        interceptor_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem", "dynamodb:Query"], resources=[table("burn-exposures")]))
        interceptor_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:BatchGetItem"], resources=[table("burn-fingerprints")]))
        interceptor_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[arn("secretsmanager", "secret:burn-system/fingerprint-key-*")]))

        # --- Gateway ---------------------------------------------------------
        gateway_role = iam.Role(
            self, "GatewayRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {"aws:SourceArn": arn("bedrock-agentcore", f"gateway/{GATEWAY_NAME}-*")},
                },
            ),
        )
        gateway_role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[tools_fn.function_arn, interceptor_fn.function_arn]))

        discovery_url = ssm.StringParameter.value_for_string_parameter(self, "/burn/identity/discovery-url")
        scope = ssm.StringParameter.value_for_string_parameter(self, "/burn/identity/scope")

        gateway = agentcore.CfnGateway(
            self, "Gateway",
            name=GATEWAY_NAME,
            description="Burn: every agent tool call passes Burn's checks here",
            role_arn=gateway_role.role_arn,
            protocol_type="MCP",
            protocol_configuration=agentcore.CfnGateway.GatewayProtocolConfigurationProperty(
                mcp=agentcore.CfnGateway.MCPGatewayConfigurationProperty(
                    streaming_configuration=agentcore.CfnGateway.StreamingConfigurationProperty(
                        enable_response_streaming=False),
                ),
            ),
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=discovery_url, allowed_scopes=[scope]),
            ),
            interceptor_configurations=[agentcore.CfnGateway.GatewayInterceptorConfigurationProperty(
                interception_points=["REQUEST", "RESPONSE"],
                interceptor=agentcore.CfnGateway.InterceptorConfigurationProperty(
                    lambda_=agentcore.CfnGateway.LambdaInterceptorConfigurationProperty(
                        arn=interceptor_fn.function_arn)),
                input_configuration=agentcore.CfnGateway.InterceptorInputConfigurationProperty(
                    pass_request_headers=True),
            )],
        )
        gateway.node.add_dependency(gateway_role)  # role + its policy exist before the gateway

        agentcore.CfnGatewayTarget(
            self, "ToolsTarget",
            gateway_identifier=gateway.attr_gateway_identifier,
            name=TARGET_NAME,
            description="Burn mock tools (contracts/mcp-tools.json)",
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=tools_fn.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=load_tool_definitions()),
                    ),
                ),
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE")],
        )

        for name, value in {"url": gateway.attr_gateway_url,
                            "id": gateway.attr_gateway_identifier,
                            "target-name": TARGET_NAME}.items():
            ssm.StringParameter(self, f"Param-{name}", parameter_name=f"/burn/gateway/{name}",
                                string_value=value)
