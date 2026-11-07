#!/usr/bin/env python3
"""Write .env and dashboard/.env.local from what is deployed in AWS.

Values come from SSM (/burn/*) and Secrets Manager, never from the repo. A missing
value is left blank with a comment so the script works while only some stacks exist.
Both output files are gitignored.
"""
import pathlib

import boto3

REPO = pathlib.Path(__file__).resolve().parents[2]
REGION = "us-east-1"

# env var -> SSM parameter published by the stack that owns it
SSM_VARS = {
    "BURN_API_URL": "/burn/api/url",
    "BURN_GATEWAY_URL": "/burn/gateway/url",
    "BURN_PROVIDER_URL": "/burn/provider/url",
    "BURN_COGNITO_TOKEN_URL": "/burn/identity/token-url",
}


def _lookup(fetch) -> str:
    try:
        return fetch()
    except Exception:  # not deployed yet, or no permission: leave blank
        return ""


def collect(ssm, secrets, sts) -> dict[str, str]:
    values = {name: _lookup(lambda p=param: ssm.get_parameter(Name=p)["Parameter"]["Value"])
              for name, param in SSM_VARS.items()}
    values["BURN_DEMO_TOKEN"] = _lookup(
        lambda: secrets.get_secret_value(SecretId="burn-system/demo-token")["SecretString"])
    values["AWS_ACCOUNT_ID"] = _lookup(lambda: sts.get_caller_identity()["Account"])
    return values


def render(values: dict[str, str], keys: list[str], rename: dict[str, str] | None = None) -> str:
    lines = []
    for key in keys:
        out_name = (rename or {}).get(key, key)
        if values.get(key):
            lines.append(f"{out_name}={values[key]}")
        else:
            lines.append(f"# {out_name} is not available yet (stack not deployed?)")
            lines.append(f"{out_name}=")
    return "\n".join(lines) + "\n"


def main() -> None:
    values = collect(boto3.client("ssm", region_name=REGION),
                     boto3.client("secretsmanager", region_name=REGION), boto3.client("sts"))
    (REPO / ".env").write_text(render(values, [
        "BURN_API_URL", "BURN_DEMO_TOKEN", "BURN_GATEWAY_URL", "BURN_PROVIDER_URL",
        "BURN_COGNITO_TOKEN_URL", "AWS_ACCOUNT_ID"]))
    dash = REPO / "dashboard"
    dash.mkdir(exist_ok=True)
    local = render(values, ["BURN_API_URL", "BURN_DEMO_TOKEN"],
                   {"BURN_API_URL": "VITE_BURN_API_URL", "BURN_DEMO_TOKEN": "VITE_BURN_DEMO_TOKEN"})
    (dash / ".env.local").write_text(local)
    missing = [k for k, v in values.items() if not v]
    print("wrote .env and dashboard/.env.local" + (f" (blank: {', '.join(missing)})" if missing else ""))


if __name__ == "__main__":
    main()
