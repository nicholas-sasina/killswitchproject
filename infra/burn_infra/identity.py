"""BurnIdentity stack: Cognito user pool, OAuth scope and domain for agent badges."""
import aws_cdk as cdk
from constructs import Construct


class BurnIdentityStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
