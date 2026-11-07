"""BurnMinter (owner: L2 Credentials).

Will contain: burn-minter Lambda, mock API provider (API Gateway + Lambda), per-agent IAM user permissions boundary.
Placeholder from the infra skeleton. The owning lane fills this file; other
lanes do not edit it (request changes in STATUS.md).
"""
import aws_cdk as cdk
from constructs import Construct


class BurnMinterStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
