#!/usr/bin/env python3
"""The checks and the build behind the hosted dashboard. No command ever prints a token value.

A dashboard build can carry the admin demo token: Vite writes into the bundle whatever
VITE_BURN_DEMO_TOKEN holds, and `make env` puts the token in dashboard/.env.local while people
sign-in is off. A hosted dashboard is public, so it is built and checked here before it is deployed.

    build         Refuse unless people sign-in is on. Build dashboard/ with the API URL and the people
                  sign-in values from SSM, local development off and an empty demo token, all in the
                  process environment, which Vite prefers over .env files. Then fail if dashboard/dist
                  holds a demo token (from .env, dashboard/.env*, or the deployed secret), or sets the
                  demo-token header to a value. Run by `make dashboard-deploy` before it deploys.
    check-hosted  Refuse unless people sign-in is on and the hosted dashboard is live: its address is in
                  SSM and it serves a page. Run by `make dashboard-hosted` before BurnPeople and BurnApi
                  are deployed with burn:dashboard_hosted=on, because both read that address.
    url           Print the hosted dashboard's address if it is live, else nothing. `make site-deploy`
                  uses it for the website's dashboard link.
"""
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

import boto3
from botocore.exceptions import ClientError
from burn_contracts import names

REPO = pathlib.Path(__file__).resolve().parents[2]
API_URL_PARAM = "/burn/api/url"  # published by BurnApi; make_env reads it by the same name

# What the hosted build gets, in the process environment: values from SSM, then fixed ones.
BUILD_PARAMS = {
    "VITE_BURN_API_URL": API_URL_PARAM,
    "VITE_BURN_PEOPLE_DOMAIN": names.PEOPLE_DOMAIN_PARAM,
    "VITE_BURN_PEOPLE_CLIENT_ID": names.PEOPLE_CLIENT_ID_PARAM,
}
BUILD_FIXED = {"VITE_BURN_LOCAL_DEV": "0", "VITE_BURN_DEMO_TOKEN": ""}

# The header name or the Vite variable, then a non-empty string literal: {"x-burn-demo-token":"..."},
# .set("x-burn-demo-token","..."), {VITE_BURN_DEMO_TOKEN:"..."}. The name alone, as in
# headers.delete("x-burn-demo-token") or .set("x-burn-demo-token",config.demoToken), is fine.
TOKEN_SET_TO_A_VALUE = re.compile(
    rb"(?:" + re.escape(names.DEMO_TOKEN_HEADER.encode()) + rb"|VITE_BURN_DEMO_TOKEN)[\"'`]?\s*[:,=]\s*[\"'`][^\"'`]",
    re.IGNORECASE)


class Refused(Exception):
    """Why the command stops, written for the person running make."""


def param(ssm, name: str) -> str:
    """An SSM parameter's value, or "" if it does not exist. Any other error (no credentials) stops the command."""
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return ""
        raise


def require_sign_in(ssm) -> None:
    if param(ssm, names.PEOPLE_ENABLED_PARAM) != "on":
        raise Refused(f"people sign-in is not on (SSM {names.PEOPLE_ENABLED_PARAM} is not \"on\"). Until it is, "
                      "a dashboard build can carry the admin token, so the dashboard is never hosted. "
                      "Switch sign-in on first.")


def build_env(ssm, base: dict[str, str]) -> dict[str, str]:
    values = {var: param(ssm, name) for var, name in BUILD_PARAMS.items()}
    missing = [BUILD_PARAMS[var] for var, value in values.items() if not value]
    if missing:
        raise Refused(f"missing in SSM: {', '.join(missing)}. Deploy BurnApi and BurnPeople first.")
    return {**base, **values, **BUILD_FIXED}


def _env_file_values(path: pathlib.Path, key: str) -> list[str]:
    if not path.is_file():
        return []
    values = []
    for line in path.read_text().splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name.removeprefix("export ").strip() == key:
            values.append(value.strip().strip("'\""))
    return values


def demo_tokens(repo: pathlib.Path, secrets=None) -> set[str]:
    """Every demo token value a build could have picked up: .env, dashboard/.env*, and the deployed secret."""
    tokens = set(_env_file_values(repo / ".env", "BURN_DEMO_TOKEN"))
    for path in sorted((repo / "dashboard").glob(".env*")):
        tokens.update(_env_file_values(path, "VITE_BURN_DEMO_TOKEN"))
    if secrets is not None:
        try:
            tokens.add(secrets.get_secret_value(SecretId=names.DEMO_TOKEN_SECRET)["SecretString"])
        except Exception:  # not deployed, or no permission: the files are still checked
            pass
    return {token for token in tokens if token}


def find_leaks(dist: pathlib.Path, tokens: set[str]) -> list[str]:
    """One line per file in dist that holds a demo token or sets the header to a value. Never the value."""
    if not (dist / "index.html").is_file():
        raise Refused(f"{dist} has no index.html: the dashboard was not built")
    encoded = [token.encode() for token in tokens]
    leaks = []
    for path in sorted(p for p in dist.rglob("*") if p.is_file()):
        data = path.read_bytes()
        if any(token in data for token in encoded):
            leaks.append(f"{path.relative_to(dist)}: holds the demo token")
        elif TOKEN_SET_TO_A_VALUE.search(data):
            leaks.append(f"{path.relative_to(dist)}: sets {names.DEMO_TOKEN_HEADER} or VITE_BURN_DEMO_TOKEN to a value")
    return leaks


def build(ssm, secrets, repo: pathlib.Path | None = None, run=None) -> None:
    repo, run = repo or REPO, run or subprocess.run
    require_sign_in(ssm)
    env = build_env(ssm, dict(os.environ))
    dashboard = repo / "dashboard"
    for command in (["npm", "ci"], ["npm", "run", "build"]):
        run(command, cwd=dashboard, env=env, check=True)
    leaks = find_leaks(dashboard / "dist", demo_tokens(repo, secrets))
    if leaks:
        raise Refused("the dashboard build holds a demo token, so it is not deployed:\n  " + "\n  ".join(leaks))


def page_status(url: str) -> int | None:
    """The HTTP status url answers with (an empty distribution says 403), or None for no answer or not https."""
    if not url.startswith("https://"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def serves_a_page(url: str) -> bool:
    return page_status(url) == 200


def live_url(ssm, fetch=serves_a_page) -> str:
    url = param(ssm, names.DASHBOARD_URL_PARAM)
    return url if url and fetch(url + "/") else ""


def check_hosted(ssm, fetch=serves_a_page) -> None:
    require_sign_in(ssm)
    url = param(ssm, names.DASHBOARD_URL_PARAM)
    if not url:
        raise Refused(f"the hosted dashboard's address is not in SSM ({names.DASHBOARD_URL_PARAM}): "
                      "run make dashboard-deploy first")
    if not fetch(url + "/"):
        raise Refused(f"the hosted dashboard at {url}/ does not serve a page yet: run make dashboard-deploy first")


def main(argv: list[str] | None = None) -> int:
    command = (sys.argv[1:] if argv is None else argv) or [""]
    ssm = boto3.client("ssm", region_name=names.REGION)
    try:
        match command[0]:
            case "build":
                build(ssm, boto3.client("secretsmanager", region_name=names.REGION))
                print("dashboard/dist: built without the demo token, and holds none")
            case "check-hosted":
                check_hosted(ssm)
            case "url":
                try:
                    print(live_url(ssm))
                except Exception:  # no credentials or no stack: the website is built without the link
                    print("")
            case _:
                print("usage: hosted_dashboard.py build | check-hosted | url", file=sys.stderr)
                return 2
    except Refused as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"refusing: {' '.join(exc.cmd)} failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
