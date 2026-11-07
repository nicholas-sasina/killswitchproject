#!/usr/bin/env python3
"""Live smoke test against the deployed minter and provider.

    uv run python scripts/smoke.py

Uses a throwaway agent id, issues credentials, checks the provider accepts the
key, rotates, checks the old key is refused and the new one works, then deletes
everything it created (even if a check fails). Prints no secret values.
"""
import secrets
import sys

import boto3

import live
from burn_contracts.names import REGION


def check(condition: bool, label: str) -> None:
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        raise SystemExit(f"smoke test failed: {label}")


def run(agent: str) -> None:
    status, body = live.minter("POST", f"/agents/{agent}/credentials", {"types": ["api_key", "aws_key"]})
    check(status == 200 and len(body["credentials"]) == 2, f"issue -> {[c['secret_ref'] for c in body['credentials']]}")
    status, again = live.minter("POST", f"/agents/{agent}/credentials", {"types": ["api_key", "aws_key"]})
    check(again == body, "issue is idempotent")

    iam = boto3.client("iam", region_name=REGION)
    user = iam.get_user(UserName=f"burn-agent-{agent}")["User"]
    check(user["Path"] == "/burn-agents/" and "PermissionsBoundary" in user, "IAM user is under /burn-agents/ with the boundary")

    api_ref = f"burn/{agent}/api_key"
    first = live.secret_value(api_ref)
    status, who = live.whoami(first)
    check(status == 200 and who["agent_id"] == agent, f"provider accepts the current key (fp {live.fingerprint_prefix(first)})")

    status, rotation = live.minter("POST", f"/agents/{agent}/rotate", {"exposed_refs": []})
    check(status == 200 and sorted(rotation["rotated"]) == sorted(c["secret_ref"] for c in body["credentials"]), "rotate")

    status, who = live.whoami(first)
    check(status == 401 and who["error"] == "key rotated", "provider rejects the old key (401 key rotated)")
    check(live.secret_value(api_ref, "AWSPREVIOUS") == first, "old key is at AWSPREVIOUS")
    second = live.secret_value(api_ref)
    check(live.whoami(second)[0] == 200, f"provider accepts the new key (fp {live.fingerprint_prefix(second)})")

    keys = iam.list_access_keys(UserName=user["UserName"])["AccessKeyMetadata"]
    check(sorted(k["Status"] for k in keys) == ["Active", "Inactive"], "old AWS key is Inactive, new one Active")

    status, activity = live.minter("GET", f"/agents/{agent}/key-activity")
    check(status == 200 and isinstance(activity["events"], list), f"key-activity returns events ({len(activity['events'])})")
    check([k["status"] for k in activity["keys"]] == ["inactive", "active"], "key-activity lists old key inactive, new key active")


def main() -> int:
    agent = f"agent-smoke-{secrets.token_hex(3)}"
    print(f"throwaway agent: {agent}")
    try:
        run(agent)
    finally:
        status, body = live.minter("DELETE", f"/agents/{agent}/credentials")
        print(f"cleanup: deleted {body.get('deleted')}")
        gone = live.secret_value(f"burn/{agent}/api_key") is None
        try:
            boto3.client("iam", region_name=REGION).get_user(UserName=f"burn-agent-{agent}")
            gone = False
        except boto3.client("iam", region_name=REGION).exceptions.NoSuchEntityException:
            pass
        print("cleanup: " + ("secrets and IAM user gone" if gone else "LEFTOVERS PRESENT"))
        if not gone:
            return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
