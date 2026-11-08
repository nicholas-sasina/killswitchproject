"""`make swarm ROLE=worker N=20`: start N workers, each running under `burn run`, on the demo network.

    uv run python scripts/run_swarm.py --role worker --count 20 [--mock] [--interval 3]

Creates a join token for the role (shown nowhere: it goes only into .run/swarm.env, mode 600, gitignored),
generates the compose override and starts it with the standalone docker-compose or the compose plugin.
Workers make no model calls, so a swarm costs nothing but the stack it runs on.
--mock runs against the mock servers on the host (docker.internal) instead of the demo stack.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from common import AGENTS_DIR, REPO_ROOT, RUN_DIR, load_dotenv
from swarm_compose import build_compose, swarm_env

MOCK_API, MOCK_GATEWAY = "http://localhost:8700", "http://localhost:8701/mcp"


def compose_command() -> list[str]:
    """`docker compose` (plugin) if installed, else the standalone docker-compose."""
    plugin = subprocess.run(["docker", "compose", "version"], capture_output=True)
    return ["docker", "compose"] if plugin.returncode == 0 else ["docker-compose"]


def burn_cli(*args: str, env: dict) -> str:
    result = subprocess.run(["uv", "run", "-q", "--project", str(REPO_ROOT / "cli"), "burn", *args],
                            capture_output=True, text=True, env=env)
    if result.returncode:
        sys.exit(f"burn {' '.join(args[:3])} failed: {result.stderr.strip()} (does the role exist? try `burn apply`)")
    return result.stdout


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between a worker's tool calls")
    parser.add_argument("--mock", action="store_true", help="use the mock servers on the host")
    parser.add_argument("--dry-run", action="store_true", help="write the files and print the command, start nothing")
    args = parser.parse_args()
    if args.count < 1:
        sys.exit("--count must be at least 1")

    load_dotenv()
    api_url = os.environ.get("BURN_API_URL") or (MOCK_API if args.mock else "")
    token = os.environ.get("BURN_DEMO_TOKEN") or ("mock" if args.mock else "")
    gateway = os.environ.get("BURN_GATEWAY_URL") or (MOCK_GATEWAY if args.mock else "")
    if not (api_url and token and gateway):
        sys.exit("BURN_API_URL, BURN_DEMO_TOKEN and BURN_GATEWAY_URL are required: run `make env` first")
    cli_env = {**os.environ, "BURN_API_URL": api_url, "BURN_DEMO_TOKEN": token}

    join_token = json.loads(burn_cli("--json", "token", "create", args.role, "--uses", str(args.count),
                                     "--ttl", "3600", env=cli_env))["token"]

    write_private(RUN_DIR / "swarm.env", "".join(
        f"{k}={v}\n" for k, v in swarm_env(api_url=api_url, token=token, join_token=join_token,
                                           gateway_url=gateway, mock=args.mock).items()))
    (RUN_DIR / "compose.swarm.yml").write_text(
        yaml.safe_dump(build_compose(args.role, mock=args.mock, interval=args.interval)))
    files = ["-f", ".run/compose.swarm.yml"] if args.mock else ["-f", "docker-compose.yml", "-f", ".run/compose.swarm.yml"]
    # Paths in the override are relative to agents/, as in the base file, wherever the override itself lives.
    up = [*compose_command(), "-p", "burn-demo", "--project-directory", ".", *files]
    if args.dry_run:
        print(" ".join([*up, "up", "--build", "--scale", f"worker={args.count}", "worker"]))
        return 0
    if not args.mock:
        from run_demo import prepare_proxy_inputs  # the proxy's build context and its own credentials
        prepare_proxy_inputs()
    try:
        return subprocess.run([*up, "up", "--build", "--scale", f"worker={args.count}", "worker"], cwd=AGENTS_DIR).returncode
    except KeyboardInterrupt:
        return 0
    finally:
        # Stop only the workers: with the demo stack the proxy may be serving other agents.
        subprocess.run([*up, "rm", "-s", "-f", "worker"], cwd=AGENTS_DIR)


if __name__ == "__main__":
    sys.exit(main())
