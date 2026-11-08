"""`make swarm ROLE=worker N=20`: start N workers, each running under `burn run`, in containers.

    uv run python scripts/run_swarm.py --role worker --count 20 [--mock] [--interval 3]

The launcher joins from the host once per container with one join token, and hands each container its own values
through `docker run -e NAME` (the values live in the launcher's environment, never in argv or a file). The
containers need no AWS access. Workers make no model calls, so a swarm costs nothing.

Without --mock the workers run on the demo stack's internal network, whose one way out is the egress proxy
(start the stack first); the Burn API, token and Gateway come from the environment or .env (`make env`).
With --mock they run against the mock servers on the host, which are the only servers it will talk to.
"""
import argparse
import os
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from common import AGENTS_DIR, REPO_ROOT, load_dotenv
from swarm_launch import IMAGE, NETWORK, Api, start_workers

MOCK_API, MOCK_GATEWAY = "http://localhost:8700", "http://localhost:8701/mcp"


def require_local(url: str) -> None:
    if urlsplit(url).hostname not in ("localhost", "127.0.0.1", "::1"):
        sys.exit(f"refusing {url}: a mock swarm only talks to servers on localhost")


def docker(argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Run docker with `env` added to the launcher's environment, for `-e NAME` pass-through."""
    result = subprocess.run(argv, env={**os.environ, **(env or {})}, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"{' '.join(argv[:3])} failed: {result.stderr.strip()}")
    return result


def follow(names: list[str]) -> None:
    """Prefix and print each container's log until it exits; return when all have."""
    def tail(name: str) -> None:
        proc = subprocess.Popen(["docker", "logs", "-f", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        short = name.rsplit("-", 1)[-1]
        for line in proc.stdout:
            print(f"{short} | {line.rstrip()}", flush=True)

    threads = [threading.Thread(target=tail, args=(n,), daemon=True) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        while t.is_alive():
            t.join(0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between a worker's tool calls")
    parser.add_argument("--mock", action="store_true", help="use the mock servers on the host")
    parser.add_argument("--api-url", default=MOCK_API, help="with --mock: the mock Burn API (localhost only)")
    parser.add_argument("--gateway-url", default=MOCK_GATEWAY, help="with --mock: the mock MCP server (localhost only)")
    args = parser.parse_args()
    if args.count < 1:
        sys.exit("--count must be at least 1")

    if args.mock:
        # The environment and .env (which `make env` points at the real stack) are ignored, so a mock swarm can
        # never use the real admin token or create real sessions.
        api_url, gateway, token = args.api_url, args.gateway_url, "mock"
        for url in (api_url, gateway):
            require_local(url)
    else:
        load_dotenv()
        api_url, token, gateway = (os.environ.get(n, "") for n in ("BURN_API_URL", "BURN_DEMO_TOKEN", "BURN_GATEWAY_URL"))
        if not (api_url and token and gateway):
            sys.exit("BURN_API_URL, BURN_DEMO_TOKEN and BURN_GATEWAY_URL are required: run `make env` first")
        if subprocess.run(["docker", "network", "inspect", NETWORK], capture_output=True).returncode:
            sys.exit(f"the demo stack's network {NETWORK} does not exist: start the stack first (make demo)")

    docker(["docker", "build", "-q", "-f", str(AGENTS_DIR / "Dockerfile.swarm"), "-t", IMAGE, str(REPO_ROOT)])
    names: list[str] = []
    try:
        names = start_workers(Api(api_url, token), args.role, args.count, api_url=api_url, admin_token=token,
                              gateway_url=gateway, mock=args.mock, interval=args.interval,
                              certs_dir=str(REPO_ROOT / ".run" / "proxy-certs"), docker=docker)
        print(f"started {len(names)} worker(s) of role {args.role}; Ctrl-C stops them")
        follow(names)
    except KeyboardInterrupt:
        pass
    finally:
        if names:
            subprocess.run(["docker", "rm", "-f", *names], capture_output=True)  # only the workers
    return 0


if __name__ == "__main__":
    sys.exit(main())
