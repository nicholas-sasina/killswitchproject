"""Write local proxy credentials with restrictive permissions; never print values."""
import json
import os
from pathlib import Path
import tempfile

import boto3

from burn_contracts.names import REGION


def write_env(path: Path, values: dict[str, str]) -> None:
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("environment values must be single-line")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".proxy-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write("".join(f"{name}={value}\n" for name, value in values.items()))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    result = boto3.client("secretsmanager", region_name=REGION).get_secret_value(
        SecretId="burn-system/egress-proxy-key")
    secret = json.loads(result["SecretString"])
    values = {"AWS_ACCESS_KEY_ID": secret["access_key_id"],
              "AWS_SECRET_ACCESS_KEY": secret["secret_access_key"],
              "AWS_DEFAULT_REGION": REGION, "AWS_EC2_METADATA_DISABLED": "true"}
    ssm = boto3.client("ssm", region_name=REGION)
    for variable, parameter in (
        ("BURN_GATEWAY_URL", "/burn/gateway/url"),
        ("BURN_PROVIDER_URL", "/burn/provider/url"),
        ("BURN_COGNITO_TOKEN_URL", "/burn/identity/token-url"),
    ):
        if os.environ.get(variable):
            values[variable] = os.environ[variable]
        else:
            try:
                values[variable] = ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
            except ssm.exceptions.ParameterNotFound:
                pass
    values["BURN_SINK_URL"] = os.environ.get("BURN_SINK_URL", "http://sink:8081")
    path = Path(__file__).resolve().parents[2] / ".run" / "proxy.env"
    write_env(path, values)
    print("Wrote .run/proxy.env (mode 600).")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Could not write proxy environment; check AWS access and deployment.") from None
