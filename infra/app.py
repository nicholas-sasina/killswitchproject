#!/usr/bin/env python3
"""Burn CDK app: one stack per component so each deploys on its own.

Stacks find each other's resources by the fixed names in
contracts/python/burn_contracts/names.py, not by CloudFormation exports,
so deploying one stack never blocks updating another.

    cd infra && npx aws-cdk@2 deploy BurnData      # one stack
    cd infra && npx aws-cdk@2 deploy --all         # everything
"""
import os

import aws_cdk as cdk

from burn_infra.burn_api import BurnApiStack
from burn_infra.data import BurnDataStack
from burn_infra.egress import BurnEgressStack
from burn_infra.gateway import BurnGatewayStack
from burn_infra.identity import BurnIdentityStack
from burn_infra.minter import BurnMinterStack
from burn_infra.people import BurnPeopleStack
from burn_infra.site import BurnSiteStack

app = cdk.App()
# Account comes from the caller's credentials at deploy time; it is never
# written into the repo. Region is fixed: the event account allows only us-east-1.
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-east-1")

BurnDataStack(app, "BurnData", env=env)          # tables, system secrets
BurnIdentityStack(app, "BurnIdentity", env=env)  # Cognito badges
BurnGatewayStack(app, "BurnGateway", env=env)    # AgentCore Gateway, interceptors, tools
BurnMinterStack(app, "BurnMinter", env=env)      # minter, mock provider
BurnEgressStack(app, "BurnEgress", env=env)      # proxy's IAM identity
people = BurnPeopleStack(app, "BurnPeople", env=env)  # Cognito people (sign-in)
api = BurnApiStack(app, "BurnApi", env=env)      # Burn API
if app.node.try_get_context("burn:people_auth") == "on":
    api.add_stack_dependency(people)  # deploy order only: with sign-in on, the API reads the people values from SSM
BurnSiteStack(app, "BurnSite", env=env)          # public website (S3 + CloudFront)

for stack in app.node.children:
    if isinstance(stack, cdk.Stack):
        cdk.Tags.of(stack).add("project", "burn")

app.synth()
