"""BurnGateway stack: AgentCore Gateway, request/response interceptor Lambda, mock tool Lambda."""
import aws_cdk as cdk
from constructs import Construct


class BurnGatewayStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
