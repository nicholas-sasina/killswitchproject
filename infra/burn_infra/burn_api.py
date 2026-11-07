"""BurnApi stack: API Gateway HTTP API and Lambdas for sessions, burn, blast radius, events."""
import aws_cdk as cdk
from constructs import Construct


class BurnApiStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
