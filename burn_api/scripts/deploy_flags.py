#!/usr/bin/env python3
"""The -c flags that keep a deploy in step with what this AWS account already runs.

`make deploy` and `make deploy-<Stack>` pass them, so no checkout switches people sign-in or the hosted
dashboard back off by accident. Nothing is committed to cdk.json, so a checkout that deploys to a new
account starts with both off.

    burn:people_auth=on       when SSM names.PEOPLE_ENABLED_PARAM is "on" (BurnApi writes it from its own flag)
    burn:dashboard_hosted=on  when sign-in is on and the hosted dashboard is live: its address is in SSM
                              (names.DASHBOARD_URL_PARAM) and it serves a page

Each is "off" otherwise. An empty distribution answers 403, so it counts as not live. When the hosted
dashboard does not answer at all, its state is unknown, and this refuses rather than guess.

--people-auth on|off and --dashboard-hosted on|off (PEOPLE_AUTH=... and DASHBOARD_HOSTED=... on the make
command line) change one on purpose for that deploy.

Prints the flags on stdout and what it decided on stderr.
"""
import argparse
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from burn_contracts import names

from hosted_dashboard import Refused, page_status, param

PEOPLE_AUTH = "burn:people_auth"
DASHBOARD_HOSTED = "burn:dashboard_hosted"


def _hosted_dashboard_live(ssm, fetch) -> str:
    url = param(ssm, names.DASHBOARD_URL_PARAM)
    if not url:
        return "off"
    status = fetch(url + "/")
    if status is None or status >= 500:
        raise Refused(f"cannot tell whether the hosted dashboard at {url}/ is live: it did not answer. "
                      "Try again, or choose with DASHBOARD_HOSTED=on or DASHBOARD_HOSTED=off.")
    return "on" if status == 200 else "off"


def decide(ssm, people_auth: str | None = None, dashboard_hosted: str | None = None,
           fetch=page_status) -> dict[str, str]:
    people = people_auth or ("on" if param(ssm, names.PEOPLE_ENABLED_PARAM) == "on" else "off")
    if dashboard_hosted:
        hosted = dashboard_hosted
    elif people != "on":
        hosted = "off"  # the hosted dashboard is accepted only while sign-in is on
    else:
        hosted = _hosted_dashboard_live(ssm, fetch)
    if hosted == "on" and not param(ssm, names.DASHBOARD_URL_PARAM):
        raise Refused(f"the hosted dashboard's address is not in SSM ({names.DASHBOARD_URL_PARAM}): "
                      "run make dashboard-deploy first")
    return {PEOPLE_AUTH: people, DASHBOARD_HOSTED: hosted}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the -c flags that match this account's deployed state.")
    parser.add_argument("--people-auth", choices=["on", "off"])
    parser.add_argument("--dashboard-hosted", choices=["on", "off"])
    args = parser.parse_args(argv)
    try:
        flags = decide(boto3.client("ssm", region_name=names.REGION), args.people_auth, args.dashboard_hosted)
    except Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    except (BotoCoreError, ClientError) as exc:
        print(f"refusing: cannot read this account's sign-in state from SSM ({type(exc).__name__}), so a deploy "
              "could switch it off. Check your AWS credentials.", file=sys.stderr)
        return 1
    print(" ".join(f"-c {name}={value}" for name, value in flags.items()))
    print("deploying with " + ", ".join(f"{name}={value}" for name, value in flags.items()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
