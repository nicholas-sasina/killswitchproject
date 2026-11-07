"""`make demo`: reset, load a scenario, start sessions, write env files, start the agents.

    uv run python scripts/run_demo.py --scenario scenarios/support-desk.yaml [--step]

--step starts the ticket reader paused: press Enter to let it take one ticket.
Secrets only ever go into .run/<agent>.env (chmod 600, gitignored).
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

import yaml

from common import AGENTS_DIR, RUN_DIR, burn_api, names, secret_value, setting
from compose_gen import build_compose
from load_scenario import load
from reset import reset


def start_session(agent_id: str) -> dict:
    resp = burn_api("POST", "/sessions", json={"agent_id": agent_id})
    resp.raise_for_status()
    return resp.json()


def agent_env(agent_id: str, session: dict, gateway_url: str, provider_url: str) -> dict:
    """The values one agent container needs. Read from Secrets Manager; never printed."""
    badge = json.loads(secret_value(session["badge_ref"]))
    env = {
        "BURN_GATEWAY_URL": gateway_url, "BURN_PROVIDER_URL": provider_url,
        "BURN_TOKEN_URL": badge["token_url"], "BURN_CLIENT_ID": badge["client_id"],
        "BURN_CLIENT_SECRET": badge["client_secret"], "BURN_SCOPE": badge.get("scope", ""),
    }
    if os.environ.get("BURN_MODEL_ID"):
        env["BURN_MODEL_ID"] = os.environ["BURN_MODEL_ID"]
    for cred in session["credentials"]:
        value = secret_value(cred["secret_ref"])
        if cred["type"] == "api_key":
            env["BURN_API_KEY"] = value
        elif cred["type"] == "aws_key":
            key = json.loads(value)
            env["BURN_AWS_ACCESS_KEY_ID"] = key["access_key_id"]
            env["BURN_AWS_SECRET_ACCESS_KEY"] = key["secret_access_key"]
    return env


def write_env_file(path: Path, env: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.writelines(f"{k}={v}\n" for k, v in env.items())
    os.chmod(path, 0o600)


def compose(*args: str, check: bool = True) -> None:
    files = ["-f", "docker-compose.yml", "-f", ".run/compose.agents.yml"]
    subprocess.run(["docker", "compose", *files, *args], cwd=AGENTS_DIR, check=check)


def step_prompt(reader_ids: list[str]) -> None:
    (RUN_DIR / "step").mkdir(exist_ok=True)
    print("Step mode: Enter = reader takes one ticket, q = stop everything.")
    while input("> ").strip().lower() != "q":
        for agent_id in reader_ids:
            (RUN_DIR / "step" / f"{agent_id}.go").touch()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="scenarios/support-desk.yaml")
    parser.add_argument("--step", action="store_true", help="advance the ticket reader one ticket per Enter")
    args = parser.parse_args()
    scenario_path = AGENTS_DIR / args.scenario if not Path(args.scenario).is_absolute() else Path(args.scenario)

    scenario_agent_ids = [a["id"] for a in yaml.safe_load(scenario_path.read_text())["agents"]]
    reset(extra_agent_ids=scenario_agent_ids)
    scenario = load(scenario_path)

    gateway_url = setting("BURN_GATEWAY_URL", "/burn/gateway/url")
    provider_url = setting("BURN_PROVIDER_URL", "/burn/provider/url")
    (RUN_DIR / "step").mkdir(parents=True, exist_ok=True)
    sessions = {}
    for agent in scenario["agents"]:
        session = start_session(agent["id"])
        sessions[agent["id"]] = session["session_id"]
        write_env_file(RUN_DIR / f"{agent['id']}.env", agent_env(agent["id"], session, gateway_url, provider_url))
        print(f"{agent['id']} ({agent['role']}): session {session['session_id']}")

    generated = build_compose(scenario, scenario_path.name, sessions, args.step)
    (RUN_DIR / "compose.agents.yml").write_text(yaml.safe_dump(generated))

    if not args.step:
        compose("up", "--build")
        return
    compose("up", "--build", "-d")
    readers = [a["id"] for a in scenario["agents"] if "list_tickets" in a["tools"]]
    try:
        step_prompt(readers)
    finally:
        compose("down", check=False)


if __name__ == "__main__":
    main()
