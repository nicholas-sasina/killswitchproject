"""Starting swarm workers: join once per container from the host, then hand each container its own values.

The launcher holds every worker's secrets in its own environment. It makes one person-side call (creating the join
token), signed in as the automation person, and never gives that sign-in to a container: workers join with the join token and
afterwards authenticate with their own session token. `docker run -e NAME` (a name
with no value) copies each from that environment into the container, so no secret is ever on a command line (visible
in `ps`) or in a file. The container's `burn run` receives the secrets as BURN_ADOPT_SECRETS, takes them out of its
environment, and gives its worker only the worker's own values.
"""
import json
import sys
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "cli"))  # the CLI's API client (standard library only)

from burn_cli import retry  # noqa: E402
from burn_cli.api import Api  # noqa: E402
from burn_cli.runner.instance_api import InstanceApi  # noqa: E402

class PersonApi(Api):
    """The client for the launcher's person-side call, authenticated by `headers()` (see run_swarm.py)."""

    def __init__(self, base_url: str, headers: Callable[[], dict]):
        super().__init__(base_url, "")
        self._headers = headers

    def auth_headers(self) -> dict:
        return self._headers()


HOST = "host.docker.internal"
IMAGE = "burn-swarm-worker"
NETWORK = "burn-demo_agents_internal"  # the demo stack's internal network: its one way out is the egress proxy
PROXY = "http://proxy:8080"
CERT = "/certs/mitmproxy-ca-cert.pem"
ADOPT_SECRETS = "BURN_ADOPT_SECRETS"  # must match burn_cli.runner.run.ADOPT_SECRETS
WORKER = ["python", "-m", "burn_agents.swarm_worker"]


def to_container_url(url: str) -> str:
    """A mock server on the host's localhost, as a container reaches it."""
    parts = urlsplit(url)
    if parts.hostname in ("localhost", "127.0.0.1", "::1"):
        parts = parts._replace(netloc=HOST + (f":{parts.port}" if parts.port else ""))
    return urlunsplit(parts)


def split_reply(reply: dict) -> tuple[dict, dict]:
    """(public session fields, secrets) from a POST /sessions reply. Only the first goes on a command line."""
    public = {k: v for k, v in reply.items() if k != "secrets"}
    secrets = reply.get("secrets")
    if not secrets:
        raise ValueError("API too old: no secrets in the join reply")
    return public, secrets


def container_env(*, public: dict, secrets: dict, api_url: str, gateway_url: str,
                  mock: bool, interval: float) -> dict[str, str]:
    """Everything one container is given, secrets included. Handed to docker through its environment only."""
    if mock:
        api_url, gateway_url = to_container_url(api_url), to_container_url(gateway_url)
        badge = {**secrets["badge"], "token_url": to_container_url(secrets["badge"]["token_url"])}
        secrets = {**secrets, "badge": badge}
    env = {
        "BURN_API_URL": api_url,  # no person token: the supervisor authenticates with the instance's session token
        "BURN_GATEWAY_URL": gateway_url, "BURN_WORKER_INTERVAL": str(interval),
        ADOPT_SECRETS: json.dumps(secrets),
    }
    if not mock:
        identity = f"http://{public['agent_id']}:{public['session_id']}@proxy:8080"  # how the proxy tells agents apart
        env.update({"BURN_PROXY_URL": PROXY, "HTTPS_PROXY": identity, "HTTP_PROXY": identity,
                    "https_proxy": identity, "http_proxy": identity, "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost", "SSL_CERT_FILE": CERT, "REQUESTS_CA_BUNDLE": CERT})
    return env


def docker_run_argv(name: str, role: str, public: dict, env_names: list[str], *, mock: bool,
                    certs_dir: str = "") -> list[str]:
    """`docker run` for one worker. Environment variables are passed by name; their values are not in this list."""
    argv = ["docker", "run", "-d", "--name", name, "--label", "burn-swarm=1"]
    argv += ["--add-host", f"{HOST}:host-gateway"] if mock else ["--network", NETWORK, "-v", f"{certs_dir}:/certs:ro"]
    for env_name in env_names:
        argv += ["-e", env_name]
    # `public` is the session without its secrets: ids, role, tools. Safe to show in `ps`.
    return argv + [IMAGE, "burn", "run", "--role", role, "--adopt", json.dumps(public), "--", *WORKER]


def start_workers(api: Api, instances: InstanceApi, role: str, count: int, *, api_url: str, gateway_url: str,
                  mock: bool, interval: float, certs_dir: str, docker: Callable[[list[str], dict], object],
                  sleep=None) -> list[str]:
    """Join `count` times with one join token and start one container per instance. Returns the container names.

    A session whose container could not be started is ended, so a failure does not leave instances dangling.
    """
    token = api.request("POST", f"/roles/{role}/tokens", {"uses": count, "ttl": 3600})["token"]  # the person-side call
    names, joined = [], {}
    try:
        for i in range(count):
            reply = retry.call(lambda: instances.join(role, token), **({"sleep": sleep} if sleep else {}))
            joined[reply["session_id"]] = (reply.get("secrets") or {}).get("session_token", "")
            public, secrets = split_reply(reply)
            env = container_env(public=public, secrets=secrets, api_url=api_url,
                                gateway_url=gateway_url, mock=mock, interval=interval)
            name = f"burn-swarm-{role}-{public['agent_id'].rsplit('-', 1)[-1]}"
            docker(docker_run_argv(name, role, public, sorted(env), mock=mock, certs_dir=certs_dir), env)
            names.append(name)
            joined.pop(public["session_id"])  # started: its supervisor owns the session now
    except BaseException:
        if names:  # do not leave the workers that did start running with nobody watching them
            try:
                docker(["docker", "rm", "-f", *names], {})
            except BaseException:  # noqa: BLE001
                pass
        raise
    finally:
        for session_id, session_token in joined.items():  # joined but never started: end them with their own token
            try:
                instances.exit(session_id, session_token)
            except Exception:  # noqa: BLE001 - best effort while already failing
                pass
    return names
