"""BurnGateway (owner: L1 Identity + Gateway).

Will contain: AgentCore Gateway, request/response interceptor Lambdas, mock tool Lambda target.
Placeholder from the infra skeleton. The owning lane fills this file; other
lanes do not edit it (request changes in STATUS.md).
"""
import aws_cdk as cdk
from constructs import Construct


class BurnGatewayStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
