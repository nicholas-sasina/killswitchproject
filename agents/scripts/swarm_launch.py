"""Starting swarm workers: join once per container from the host, then hand each container its own values.

The launcher holds every worker's secrets in its own environment. It makes one person-side call (creating the join
token), signed in as the automation person, and never gives that sign-in to a container: workers join with the join token and
afterwards authenticate with their own session token. `docker run -e NAME` (a name
with no value) copies each from that environment into the container, so no secret is ever on a command line (visible
in `ps`) or in a file. The container's `burn run` receives the secrets as BURN_ADOPT_SECRETS, takes them out of its
environment, and gives its worker only the worker's own values.

With a task (`--items`), each worker gets its own list of item ids as BURN_TASK, and the launcher keeps the map of
container to task. The task stays here: Burn never holds it, so a compromised agent cannot change it.
"""
import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "cli"))  # the CLI's API client (standard library only)

from burn_cli import retry  # noqa: E402
from burn_cli.api import Api  # noqa: E402
from burn_cli.runner.instance_api import InstanceApi  # noqa: E402
from burn_contracts.names import RESUME_ENV, TASK_ENV  # noqa: E402

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
JOIN_TTL = 3600


def task_ids(worker: int, items: int) -> list[str]:
    """Worker `worker`'s task (counting from 1): `items` item ids, empty for no task."""
    return [f"w{worker:02d}-item-{i:02d}" for i in range(1, items + 1)]


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
                  mock: bool, interval: float, task: list[str] | None = None,
                  resume: dict | None = None) -> dict[str, str]:
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
    if task:  # item ids only, never secret; `burn run` checks them and passes them on to its worker
        env[TASK_ENV] = json.dumps(task)
        if resume is not None:
            env[RESUME_ENV] = json.dumps({"done": resume["done"], "quarantined": resume["quarantined"]})
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


class BurnCalls:
    """Every Burn API call the launcher makes.

    The person-side call (the join token) goes through `api`, which carries the launcher's own sign-in. Joining and ending an instance go through `instances`, which sends no person
    credentials: a join token or the instance's own session token is the authority.
    """

    def __init__(self, api: Api, instances: InstanceApi, sleep=None):
        self.api, self.instances, self._retry = api, instances, {"sleep": sleep} if sleep else {}

    def join_token(self, role: str, uses: int, ttl: int = JOIN_TTL) -> str:
        return self.api.request("POST", f"/roles/{role}/tokens", {"uses": uses, "ttl": ttl})["token"]

    def join(self, role: str, token: str) -> dict:
        return retry.call(lambda: self.instances.join(role, token), **self._retry)

    def end(self, session_id: str, session_token: str) -> None:
        self.instances.exit(session_id, session_token)


@dataclass
class Worker:
    """What the launcher knows about one container: its instance and its task (empty without one)."""
    agent_id: str
    session_id: str
    task: list[str]


class Swarm:
    """The launcher's workers: the map of container to task."""

    def __init__(self, calls: BurnCalls, role: str, *, api_url: str, gateway_url: str, mock: bool, interval: float,
                 certs_dir: str, docker: Callable[[list[str], dict], object]):
        self.calls, self.role, self.docker = calls, role, docker
        self._container = {"api_url": api_url, "gateway_url": gateway_url, "mock": mock, "interval": interval}
        self.mock, self.certs_dir = mock, certs_dir
        self.workers: dict[str, Worker] = {}
        self._lock = threading.Lock()

    @property
    def names(self) -> list[str]:
        """Every container started, so stopping the launcher removes them all."""
        with self._lock:
            return list(self.workers)

    def start(self, count: int, items: int = 0) -> list[str]:
        """Join `count` times with one join token and start one container per instance. Returns their names."""
        token = self.calls.join_token(self.role, count)
        started = []
        try:
            for k in range(1, count + 1):
                started.append(self._launch(self.calls.join(self.role, token), task_ids(k, items)))
        except BaseException:
            if started:  # do not leave the workers that did start running with nobody watching them
                try:
                    self.docker(["docker", "rm", "-f", *started], {})
                except BaseException:  # noqa: BLE001
                    pass
            raise
        return started

    def _launch(self, reply: dict, task: list[str], resume: dict | None = None) -> str:
        """Start the container for a joined instance. If it does not start, the session is ended with its own token."""
        try:
            public, secrets = split_reply(reply)
            env = container_env(public=public, secrets=secrets, task=task, resume=resume, **self._container)
            name = f"burn-swarm-{self.role}-{public['agent_id'].rsplit('-', 1)[-1]}"
            self.docker(docker_run_argv(name, self.role, public, sorted(env), mock=self.mock,
                                        certs_dir=self.certs_dir), env)
        except BaseException:
            try:
                self.calls.end(reply["session_id"], (reply.get("secrets") or {}).get("session_token", ""))
            except Exception:  # noqa: BLE001 - best effort while already failing
                pass
            raise
        with self._lock:  # started: its supervisor owns the session now
            self.workers[name] = Worker(public["agent_id"], public["session_id"], task)
        return name


def start_workers(api: Api, instances: InstanceApi, role: str, count: int, *, api_url: str, gateway_url: str,
                  mock: bool, interval: float, certs_dir: str, docker: Callable[[list[str], dict], object],
                  items: int = 0, sleep=None) -> Swarm:
    """Join `count` times with one join token and start one container per instance, each with a task of `items` items.

    A session whose container could not be started is ended, so a failure does not leave instances dangling.
    """
    swarm = Swarm(BurnCalls(api, instances, sleep=sleep), role, api_url=api_url, gateway_url=gateway_url, mock=mock,
                  interval=interval, certs_dir=certs_dir, docker=docker)
    swarm.start(count, items)
    return swarm
