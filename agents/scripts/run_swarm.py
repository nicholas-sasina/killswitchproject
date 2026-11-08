"""`make swarm ROLE=worker N=20`: start N AI workers, each running under `burn run`, in containers.

    uv run python scripts/run_swarm.py --role worker --count 20 [--items 10] [--mock] [--interval 3]
        [--max-model-calls 400] [--worker-model-calls 40]

The launcher joins from the host once per container with one join token, and hands each container its own values
through `docker run -e NAME` (the values live in the launcher's environment, never in argv or a file). The
containers need no AWS access of the host's: each worker is an AI agent (Claude Haiku 4.5 on Bedrock) whose model
runs on its instance's own AWS key. Model calls cost money, so they are capped, per worker (--worker-model-calls) and
for the whole run (--max-model-calls, replacements included); a ticket takes three to five calls, about one to two
cents of Haiku.

Without --mock the workers run on the demo stack's internal network, whose one way out is the egress proxy
(start the stack first); the Burn API and Gateway come from the environment or .env (`make env`). The role is used
if it exists (it must give each instance its own AWS key) or created with full access. The workers handle the loaded
scenario's tickets, so a scenario must be loaded; the launcher stops before starting anything if none is.
With --mock they run against the mock servers on the host, which are the only servers it will talk to, and run the
scripted worker, which makes no model calls (the mock servers have no model).

With --items each worker works through a task of that many items and exits when it is done. A worker that Burn burns
is replaced by a fresh instance that resumes its task (when the role has replace on burn); see swarm_launch.py.
"""
import argparse
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from common import AGENTS_DIR, REPO_ROOT, load_dotenv
from burn_contracts.automation import AutomationAuthError, automation_headers
from burn_contracts.names import DEMO_TOKEN_HEADER, MAX_PROGRESS_ITEMS
from swarm_launch import (DEFAULT_RUN_CALLS, DEFAULT_WORKER_CALLS, IMAGE_NAME, NETWORK, ApiError, InstanceApi,
                          PersonApi, ProxyCaError, RoleError, RunBudget, ensure_role, fetch_proxy_ca, image_tag,
                          start_workers)

MOCK_API, MOCK_GATEWAY = "http://localhost:8700", "http://localhost:8701/mcp"
LOAD_A_SCENARIO = ("load one first: `cd agents && uv run python scripts/load_scenario.py scenarios/support-desk.yaml`"
                   " (or `make demo`, which also resets the stack)")


def require_scenario(api) -> list[str]:
    """The roles of the loaded scenario's agents; exits when no scenario is loaded.

    Burn lists a scenario's agents as roles (managed_by "scenario") for as long as the scenario is loaded, and the
    Gateway's ticket tools read the same scenario, so this asks the API rather than the scenario table itself.
    """
    try:
        roles = api.request("GET", "/roles")["roles"]
    except ApiError as exc:
        sys.exit(f"cannot ask the Burn API which scenario is loaded: {exc}")
    loaded = [r["name"] for r in roles if r.get("managed_by") == "scenario"]
    if not loaded:
        sys.exit(f"no scenario is loaded, and the workers handle the loaded scenario's tickets: {LOAD_A_SCENARIO}")
    return loaded


def require_local(url: str) -> None:
    if urlsplit(url).hostname not in ("localhost", "127.0.0.1", "::1"):
        sys.exit(f"refusing {url}: a mock swarm only talks to servers on localhost")


def docker(argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Run docker with `env` added to the launcher's environment, for `-e NAME` pass-through."""
    result = subprocess.run(argv, env={**os.environ, **(env or {})}, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"{' '.join(argv[:3])} failed: {result.stderr.strip()}")
    return result


FRESH = timedelta(minutes=10)  # a tag built or tagged this recently may be another checkout's, about to run it
DOCKER_TIME = re.compile(r"(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?")


def last_built(inspected: str) -> datetime | None:
    """The later of an image's Created and Metadata.LastTagTime, as `docker image inspect` prints them, or None.

    Both count: a fully cached build keeps the image's old Created, but giving it a new tag moves LastTagTime.
    """
    found = []
    for value in inspected.strip().split("|"):
        match = DOCKER_TIME.match(value.strip())
        if not match:
            continue
        day, clock, fraction, zone = match.groups()
        zone = "+00:00" if zone in (None, "Z") else zone if ":" in zone else f"{zone[:3]}:{zone[3:]}"
        try:  # at most microseconds: docker prints nanoseconds
            found.append(datetime.fromisoformat(f"{day}T{clock}{(fraction or '')[:7]}{zone}"))
        except ValueError:
            continue
    return max(found) if found else None


def remove_other_images(keep: str, run=subprocess.run, now: datetime | None = None) -> list[str]:
    """Remove the swarm worker images that older code or other checkouts left behind, keeping `keep`. Returns them.

    Each code change builds a new tag, so they pile up. Left alone: a tag that any container (running or stopped) was
    created from, since another checkout's swarm still starts its replacements from it (`docker rmi` alone would only
    refuse an image's last tag; an image with two tags loses one even while a container uses it); and a tag built or
    tagged in the last ten minutes, which another checkout may be about to run. When docker cannot say, the tag is kept.
    Nothing is forced.
    """
    now = now or datetime.now(timezone.utc)
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
        inspected = run(["docker", "image", "inspect", "-f", "{{.Created}}|{{.Metadata.LastTagTime}}", tag],
                        capture_output=True, text=True)
        built = None if inspected.returncode else last_built(inspected.stdout)
        if built is None or now - built < FRESH:
            continue
        if run(["docker", "rmi", tag], capture_output=True, text=True).returncode == 0:
            removed.append(tag)
    return removed


def tail_logs(name: str, observe=None) -> None:
    """Print one container's log, each line prefixed with its short name, until the container exits.

    observe(name, line) sees every line (the swarm reads how many model calls the worker used)."""
    proc = subprocess.Popen(["docker", "logs", "-f", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    short = name.rsplit("-", 1)[-1]
    for line in proc.stdout:
        if observe:
            observe(name, line)
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
    parser.add_argument("--max-model-calls", type=int, default=DEFAULT_RUN_CALLS,
                        help=f"hard cap on model calls for the whole run, replacements included "
                             f"(default {DEFAULT_RUN_CALLS})")
    parser.add_argument("--worker-model-calls", type=int, default=DEFAULT_WORKER_CALLS,
                        help=f"hard cap on each worker's model calls (default {DEFAULT_WORKER_CALLS})")
    parser.add_argument("--mock", action="store_true",
                        help="use the mock servers on the host, with the scripted worker (no model calls)")
    parser.add_argument("--api-url", default=MOCK_API, help="with --mock: the mock Burn API (localhost only)")
    parser.add_argument("--gateway-url", default=MOCK_GATEWAY, help="with --mock: the mock MCP server (localhost only)")
    args = parser.parse_args()
    if args.count < 1:
        sys.exit("--count must be at least 1")
    if not 0 <= args.items <= MAX_PROGRESS_ITEMS:
        sys.exit(f"--items must be between 0 and {MAX_PROGRESS_ITEMS}, the most items Burn tracks for one instance")
    budget = None
    if not args.mock:
        try:
            budget = RunBudget(args.max_model_calls, args.worker_model_calls, args.count)
        except ValueError as exc:
            sys.exit(f"{exc} (--max-model-calls, --worker-model-calls)")

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
        require_scenario(api)  # before the role: a fresh role must not be created for a swarm that cannot run
        try:
            ensure_role(api, args.role)
        except (RoleError, ApiError) as exc:
            sys.exit(f"role {args.role}: {exc}" if isinstance(exc, ApiError) else str(exc))
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
                              items=args.items, budget=budget)
        task = f", {args.items} item(s) each" if args.items else ""
        model = (" scripted (no model calls)" if args.mock else
                 f" AI (at most {budget.per_worker} model calls each, {budget.total} for the run)")
        print(f"started {len(swarm.names)}{model} worker(s) of role {args.role}{task}; Ctrl-C stops them")
        removed = remove_other_images(image)  # now: this swarm's containers hold its own image
        if removed:
            print(f"removed {len(removed)} unused swarm image(s) built from other code", flush=True)
        follow(swarm.names, swarm.on_exit, tail=lambda name: tail_logs(name, swarm.observe))
    except KeyboardInterrupt:
        pass
    finally:
        if swarm and swarm.names:
            subprocess.run(["docker", "rm", "-f", *swarm.names], capture_output=True)  # only the workers
    return 0


if __name__ == "__main__":
    sys.exit(main())
