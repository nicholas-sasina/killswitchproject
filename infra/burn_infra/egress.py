"""BurnEgress stack: least-privilege IAM identity for the local egress proxy."""
import aws_cdk as cdk
from aws_cdk import aws_iam as iam, aws_secretsmanager as sm, aws_ssm as ssm
from constructs import Construct


class BurnEgressStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        def table(name: str) -> str:
            return self.format_arn(service="dynamodb", resource="table", resource_name=name)

        user = iam.User(self, "ProxyUser", user_name="burn-egress-proxy", path="/burn-system/")
        user.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:Query"],
            resources=[table("burn-sessions"), table("burn-sessions") + "/index/by_agent",
                       table("burn-sessions") + "/index/by_client"],
        ))
        user.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:Scan", "dynamodb:BatchGetItem"], resources=[table("burn-fingerprints")],
        ))
        user.add_to_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem"], resources=[table("burn-events")],
        ))
        user.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"], resources=[self.format_arn(
                service="secretsmanager", resource="secret", resource_name="burn-system/fingerprint-key-??????",
                arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME)],
        ))
        access_key = iam.AccessKey(self, "ProxyAccessKey", user=user)
        resource = access_key.node.default_child
        secret = sm.Secret(self, "ProxyCredentials", secret_name="burn-system/egress-proxy-key",
            description="Credentials for the authenticated Burn egress proxy",
            secret_object_value={
                "access_key_id": cdk.SecretValue.resource_attribute(access_key.access_key_id),
                "secret_access_key": cdk.SecretValue.resource_attribute(resource.attr_secret_access_key),
            }, removal_policy=cdk.RemovalPolicy.DESTROY)
        ssm.StringParameter(self, "ProxyCredentialsName", parameter_name="/burn/egress/credentials-secret-name",
                            string_value=secret.secret_name)
