"""`make swarm ROLE=worker N=20`: start N workers, each running under `burn run`, in containers.

    uv run python scripts/run_swarm.py --role worker --count 20 [--items 10] [--mock] [--interval 3]

The launcher joins from the host once per container with one join token, and hands each container its own values
through `docker run -e NAME` (the values live in the launcher's environment, never in argv or a file). The
containers need no AWS access. Workers make no model calls, so a swarm costs nothing.

Without --mock the workers run on the demo stack's internal network, whose one way out is the egress proxy
(start the stack first); the Burn API and Gateway come from the environment or .env (`make env`).
With --mock they run against the mock servers on the host, which are the only servers it will talk to.

With --items each worker works through a task of that many items and exits when it is done. A worker that Burn burns
is replaced by a fresh instance that resumes its task (when the role has replace on burn); see swarm_launch.py.
"""
import argparse
import os
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from common import AGENTS_DIR, REPO_ROOT, load_dotenv
from burn_contracts.automation import AutomationAuthError, automation_headers
from burn_contracts.names import DEMO_TOKEN_HEADER, MAX_PROGRESS_ITEMS
from swarm_launch import (IMAGE_NAME, NETWORK, InstanceApi, PersonApi, ProxyCaError, fetch_proxy_ca, image_tag,
                          start_workers)

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


def remove_other_images(keep: str, run=subprocess.run) -> list[str]:
    """Remove the swarm worker images that older code or other checkouts left behind, keeping `keep`. Returns them.

    Each code change builds a new tag, so they pile up. A tag that any container (running or stopped) was created from
    is left alone: another checkout's swarm still starts its replacements from it. (`docker rmi` alone would only refuse
    an image's last tag; an image with two tags loses one even while a container uses it.) Nothing is forced.
    """
    listed = run(["docker", "image", "ls", IMAGE_NAME, "--format", "{{.Repository}}:{{.Tag}}"],
                 capture_output=True, text=True)
    if listed.returncode:
        return []
    removed = []
    for tag in listed.stdout.split():
        if tag == keep or tag.endswith(":<none>"):
            continue
        users = run(["docker", "ps", "-a", "-q", "--filter", f"ancestor={tag}"], capture_output=True, text=True)
        if users.returncode or users.stdout.strip():
            continue  # in use, or docker could not say: keep it
        if run(["docker", "rmi", tag], capture_output=True, text=True).returncode == 0:
            removed.append(tag)
    return removed


def tail_logs(name: str) -> None:
    """Print one container's log, each line prefixed with its short name, until the container exits."""
    proc = subprocess.Popen(["docker", "logs", "-f", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    short = name.rsplit("-", 1)[-1]
    for line in proc.stdout:
        print(f"{short} | {line.rstrip()}", flush=True)


def follow(names: list[str], on_exit=None, tail=tail_logs) -> None:
    """Follow each worker until it exits, then the container `on_exit(name)` started in its place, if any.

    Each worker has a thread of its own, so one replacement never waits on another. Returns when none is left.
    """
    def lane(name: str | None) -> None:
        while name:
            tail(name)
            try:
                name = on_exit(name) if on_exit else None
            except Exception as exc:  # noqa: BLE001 - say so rather than let the thread end silently
                print(f"{name}: {exc}", flush=True)
                name = None

    threads = [threading.Thread(target=lane, args=(n,), daemon=True) for n in names]
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
    parser.add_argument("--items", type=int, default=0,
                        help="items in each worker's task, replaced on burn (default 0: no task, poll until stopped)")
    parser.add_argument("--mock", action="store_true", help="use the mock servers on the host")
    parser.add_argument("--api-url", default=MOCK_API, help="with --mock: the mock Burn API (localhost only)")
    parser.add_argument("--gateway-url", default=MOCK_GATEWAY, help="with --mock: the mock MCP server (localhost only)")
    args = parser.parse_args()
    if args.count < 1:
        sys.exit("--count must be at least 1")
    if not 0 <= args.items <= MAX_PROGRESS_ITEMS:
        sys.exit(f"--items must be between 0 and {MAX_PROGRESS_ITEMS}, the most items Burn tracks for one instance")

    if args.mock:
        # The environment and .env (which `make env` points at the real stack) are ignored, so a mock swarm can
        # never sign in to the real stack or create real sessions.
        api_url, gateway = args.api_url, args.gateway_url
        api = PersonApi(api_url, lambda: {DEMO_TOKEN_HEADER: "mock"})
        for url in (api_url, gateway):
            require_local(url)
    else:
        load_dotenv()
        api_url, gateway = (os.environ.get(n, "") for n in ("BURN_API_URL", "BURN_GATEWAY_URL"))
        if not (api_url and gateway):
            sys.exit("BURN_API_URL and BURN_GATEWAY_URL are required: run `make env` first")
        api = PersonApi(api_url, automation_headers)  # the join-token call, as the automation person
        try:
            automation_headers()  # sign in once now, so a failure stops here rather than midway
        except AutomationAuthError as exc:
            sys.exit(f"cannot authenticate to the Burn API: {exc}")
        if subprocess.run(["docker", "network", "inspect", NETWORK], capture_output=True).returncode:
            sys.exit(f"the demo stack's network {NETWORK} does not exist: start the stack first (make demo)")
    certs_dir = ""
    if not args.mock:
        # The CA of the proxy that is running, wherever the stack was started from (see fetch_proxy_ca).
        try:
            certs_dir = fetch_proxy_ca(lambda argv: docker(argv).stdout, str(REPO_ROOT / ".run" / "swarm-proxy-ca"))
        except ProxyCaError as exc:
            sys.exit(str(exc))

    image = image_tag()  # this checkout's own image: another checkout's swarm cannot replace it under us
    docker(["docker", "build", "-q", "-f", str(AGENTS_DIR / "Dockerfile.swarm"), "-t", image, str(REPO_ROOT)])
    swarm = None
    try:
        swarm = start_workers(api, InstanceApi(api_url), args.role, args.count, api_url=api_url,
                              gateway_url=gateway, mock=args.mock, interval=args.interval,
                              certs_dir=certs_dir, docker=docker, image=image,
                              items=args.items)
        task = f", {args.items} item(s) each" if args.items else ""
        print(f"started {len(swarm.names)} worker(s) of role {args.role}{task}; Ctrl-C stops them")
        removed = remove_other_images(image)  # now: this swarm's containers hold its own image
        if removed:
            print(f"removed {len(removed)} unused swarm image(s) built from other code", flush=True)
        follow(swarm.names, swarm.on_exit)
    except KeyboardInterrupt:
        pass
    finally:
        if swarm and swarm.names:
            subprocess.run(["docker", "rm", "-f", *swarm.names], capture_output=True)  # only the workers
    return 0


if __name__ == "__main__":
    sys.exit(main())
