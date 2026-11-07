"""BurnEgress stack: least-privilege IAM identity for the local egress proxy."""
import aws_cdk as cdk
from constructs import Construct


class BurnEgressStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
