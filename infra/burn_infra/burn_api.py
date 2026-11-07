"""BurnApi (owner: L6 Burn API).

Will contain: API Gateway HTTP API + Lambdas for sessions, burn, blast radius, events.
Placeholder from the infra skeleton. The owning lane fills this file; other
lanes do not edit it (request changes in STATUS.md).
"""
import aws_cdk as cdk
from constructs import Construct


class BurnApiStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
