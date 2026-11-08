#!/usr/bin/env python3
"""Standalone CDK app for the notifications stack, so deploying it needs no change to infra/app.py.

    cd notifications
    uv run --group infra npx -y aws-cdk@2 synth --app "python infra/app.py" -c burn:events_stream_arn=...
"""
import os
import sys
from pathlib import Path

import aws_cdk as cdk

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notifications_stack import BurnNotificationsStack  # noqa: E402

app = cdk.App()
# Account from the caller's credentials at deploy time; region fixed like the rest of Burn.
env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region="us-east-1")
stack = BurnNotificationsStack(app, "BurnNotifications", env=env)
cdk.Tags.of(stack).add("project", "burn")
cdk.Tags.of(stack).add("component", "notifications")
app.synth()
