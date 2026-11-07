"""BurnMinter stack: credential minter Lambda and the mock API provider."""
import aws_cdk as cdk
from constructs import Construct


class BurnMinterStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
