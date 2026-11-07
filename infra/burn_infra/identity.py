"""BurnIdentity (owner: L1 Identity + Gateway).

Will contain: Cognito user pool, resource server/scope, domain (badges).
Placeholder from the infra skeleton. The owning lane fills this file; other
lanes do not edit it (request changes in STATUS.md).
"""
import aws_cdk as cdk
from constructs import Construct


class BurnIdentityStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
