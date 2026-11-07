#!/usr/bin/env python3
"""Replay an agent's pre-rotation credentials and show that they are dead.

    uv run python scripts/replay_old_key.py --agent agent-reader

The old values are read from the AWSPREVIOUS version in Secrets Manager, so
nobody had to keep a copy. Expect: the provider answers 401, and AWS answers
InvalidClientTokenId for the old AWS key. Prints only secret_refs,
fingerprint prefixes and status codes, never a key.
"""
import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

import live
from burn_contracts.names import agent_secret_ref


def replay_api_key(agent_id: str) -> bool:
    ref = agent_secret_ref(agent_id, "api_key")
    old = live.secret_value(ref, "AWSPREVIOUS")
    if old is None:
        print(f"{ref}: no previous version (the agent has not been burned yet)")
        return False
    status, body = live.whoami(old)
    print(f"{ref} previous (fp {live.fingerprint_prefix(old)}): provider -> {status} {body.get('error', '')}")
    return status == 401


def replay_aws_key(agent_id: str) -> bool:
    ref = agent_secret_ref(agent_id, "aws_key")
    raw = live.secret_value(ref, "AWSPREVIOUS")
    if raw is None:
        print(f"{ref}: no previous version (the agent has not been burned yet)")
        return False
    old = json.loads(raw)
    sts = boto3.client("sts", region_name="us-east-1", aws_access_key_id=old["access_key_id"],
                       aws_secret_access_key=old["secret_access_key"])
    try:
        sts.get_caller_identity()
        outcome, rejected = "ACCEPTED (key is still live!)", False
    except ClientError as err:
        code = err.response["Error"]["Code"]
        outcome, rejected = code, code == "InvalidClientTokenId"
    print(f"{ref} previous (fp {live.fingerprint_prefix(old['access_key_id'])}): sts:GetCallerIdentity -> {outcome}")
    return rejected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", required=True, help="agent id, e.g. agent-reader")
    agent_id = parser.parse_args().agent
    results = [replay_api_key(agent_id), replay_aws_key(agent_id)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
