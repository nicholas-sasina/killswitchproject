#!/usr/bin/env python3
"""Replay an agent's pre-rotation credentials and show that they are dead.

    uv run python scripts/replay_old_key.py --agent agent-reader

Replays only the credential types the agent holds, as Burn records them in burn-credentials
(claude-NN instances and Full-access roles hold just an aws_key). The old values are read from
the AWSPREVIOUS version in Secrets Manager, so nobody had to keep a copy. Expect: the provider
answers 401 for the old api_key, and AWS answers InvalidClientTokenId for the old AWS key.
Exits 1 if a held credential has no previous version or an old one still works.
Prints only secret_refs, fingerprint prefixes and status codes, never a key.
"""
import argparse
import json
import sys

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import live
from burn_contracts.names import CREDENTIALS_TABLE, REGION, agent_secret_ref


def replay_api_key(agent_id: str) -> bool:
    ref = agent_secret_ref(agent_id, "api_key")
    old = live.secret_value(ref, "AWSPREVIOUS")
    if old is None:
        print(f"{ref}: no previous version (the agent has not been burned yet)")
        return False
    status, body = live.whoami(old)
    print(f"{ref} previous (fp {live.fingerprint_prefix(old)}): provider -> {status} {body.get('error', '')}")
    return status == 401


def sts_error_code(key: dict) -> str | None:
    """The error AWS gives sts:GetCallerIdentity signed with this key, or None if it answers."""
    sts = boto3.client("sts", region_name=REGION, aws_access_key_id=key["access_key_id"],
                       aws_secret_access_key=key["secret_access_key"])
    try:
        sts.get_caller_identity()
        return None
    except ClientError as err:
        return err.response["Error"]["Code"]


def replay_aws_key(agent_id: str) -> bool:
    ref = agent_secret_ref(agent_id, "aws_key")
    raw = live.secret_value(ref, "AWSPREVIOUS")
    if raw is None:
        print(f"{ref}: no previous version (the agent has not been burned yet)")
        return False
    old = json.loads(raw)
    code = sts_error_code(old)
    outcome = "ACCEPTED (key is still live!)" if code is None else code
    print(f"{ref} previous (fp {live.fingerprint_prefix(old['access_key_id'])}): sts:GetCallerIdentity -> {outcome}")
    return code == "InvalidClientTokenId"


REPLAYS = {"api_key": replay_api_key, "aws_key": replay_aws_key}


def held_types(agent_id: str) -> list[str]:
    """The replayable credential types burn-credentials records for the agent (canaries never authenticate)."""
    table = boto3.resource("dynamodb", region_name=REGION).Table(CREDENTIALS_TABLE)
    kwargs = {"IndexName": "by_owner", "KeyConditionExpression": Key("owner_agent_id").eq(agent_id),
              "ProjectionExpression": "secret_type"}
    types = set()
    while True:  # one canary row per session, so an agent can outgrow one page
        resp = table.query(**kwargs)
        types |= {row["secret_type"] for row in resp["Items"]}
        if "LastEvaluatedKey" not in resp:
            return sorted(types & REPLAYS.keys())
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", required=True, help="agent id, e.g. agent-reader")
    agent_id = parser.parse_args(argv).agent
    held = held_types(agent_id)
    if not held:
        print(f"{agent_id}: no api_key or aws_key on record (unknown agent, or its credentials were deleted)")
        return 1
    print(f"{agent_id} holds: {', '.join(held)}")
    results = [REPLAYS[secret_type](agent_id) for secret_type in held]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
