"""Starting swarm workers: join once per container from the host, then hand each container its own values.

The launcher holds every worker's secrets in its own environment. Its person-side calls (creating join tokens and,
with a task, reading and replacing a burned instance) are signed in as the automation person, and it never gives that
sign-in to a container: workers join with a join token and afterwards authenticate with their own session token. `docker run -e NAME` (a name
with no value) copies each from that environment into the container, so no secret is ever on a command line (visible
in `ps`) or in a file. The container's `burn run` receives the secrets as BURN_ADOPT_SECRETS, takes them out of its
environment, and gives its worker only the worker's own values.

With a task (`--items`), each worker gets its own list of item ids as BURN_TASK, and the launcher keeps the map of
container to task. When a worker's container exits and Burn says the instance was burned with a replacement pending,
the launcher starts the replacement: one fresh join, POST /instances/{old}/replace, and a new container with the same
task and BURN_RESUME (what the burned instance finished, and what it touched after the breach). The task stays here:
Burn never holds it, so a compromised agent cannot change it.

Each worker is an AI agent whose model calls cost money, so the run has a hard cap on them (RunBudget): each
container is started with its share (`--max-model-calls`), which the worker never exceeds, and the shares started
never add up to more than the run's cap. When a worker exits, the share it did not use (its last log line says how
many it used) goes back to the run, and a replacement continues on what is left. `--mock` runs the scripted worker,
which makes no model calls.
"""
import hashlib
import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "cli"))  # the CLI's API client (standard library only)

from burn_agents.swarm_worker import DEFAULT_WORKER_CALLS, USAGE_RE  # noqa: E402
from burn_cli import retry  # noqa: E402
from burn_cli.api import Api, ApiError  # noqa: E402
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
IMAGE_NAME = "burn-swarm-worker"
# What Dockerfile.swarm copies into the image, relative to its build context (the repository root).
IMAGE_INPUTS = ("agents/Dockerfile.swarm", "agents/swarm-entrypoint.sh", "agents/burn_agents", "contracts", "cli")
NOT_IN_IMAGE = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", "tests", "node_modules"}
NETWORK = "burn-demo_agents_internal"  # the demo stack's internal network: its one way out is the egress proxy
PROXY = "http://proxy:8080"
CA_FILE = "mitmproxy-ca-cert.pem"  # the proxy CA's public certificate (mitmproxy-ca.pem would hold its key)
CERT = f"/certs/{CA_FILE}"
ADOPT_SECRETS = "BURN_ADOPT_SECRETS"  # must match burn_cli.runner.run.ADOPT_SECRETS
WORKER = ["python", "-m", "burn_agents.swarm_worker"]
DEFAULT_RUN_CALLS = 400  # model calls for a whole `make swarm`, replacements included: under $1 of Haiku
MIN_SHARE = 5  # model calls: less than this is not enough for one ticket, so a worker is not started on it
NEEDED_TOOLS = ("list_tickets", "read_ticket")  # a worker finds its tickets with these
JOIN_TTL = 3600
REPLACEMENT_JOIN_TTL = 300  # a replacement's join token is for one join, made straight away
READS, READ_GAP = 3, 5.0  # a burned instance's replacement record can land a moment after its container exits


class RoleError(Exception):
    """The role exists but cannot run AI workers. The message says what to change."""


def full_access_role(name: str) -> dict:
    """The Full access role body, with replace on burn: every tool in contracts/mcp-tools.json and an own AWS key."""
    tools = json.loads((REPO_ROOT / "contracts" / "mcp-tools.json").read_text())["tools"]
    return {"name": name, "tools": [t["name"] for t in tools], "credentials": ["aws_key"], "may_message": [],
            "may_spawn": [], "egress": None, "owner": None, "canaries": True, "auto_burn": True,
            "replace_on_burn": True}


def ensure_role(api: Api, name: str, log=print) -> dict:
    """The role the workers run as: the one that exists, or a new Full access role (with replace on burn).

    An existing role must give each instance its own AWS key (the worker's model runs on it) and the tools a worker
    finds its tickets with; otherwise RoleError, before anything starts.
    """
    try:
        role = api.request("GET", f"/roles/{name}")
    except ApiError as exc:
        if exc.status != 404:
            raise
        role = api.request("POST", "/roles", full_access_role(name))
        log(f"created role {name}: full access, its own AWS key per instance, replace on burn")
        return role
    if "aws_key" not in (role.get("credentials") or []):
        raise RoleError(f"role {name} gives its instances no AWS key of their own (credentials "
                        f"{role.get('credentials') or []}), and a worker's model runs on that key: add \"aws_key\" "
                        f"to its credentials, or use a new role name and the swarm creates a full access role")
    missing = [t for t in NEEDED_TOOLS if t not in (role.get("tools") or [])]
    if missing:
        raise RoleError(f"role {name} does not allow {', '.join(missing)}, which a worker needs to find its tickets")
    log(f"using the existing role {name}" + ("" if role.get("replace_on_burn") else " (replace on burn is off)"))
    return role


class RunBudget:
    """The run's hard cap on model calls, shared out to the workers it starts.

    Every container gets a share (`--max-model-calls`) that it never exceeds, and the shares handed out never add up
    to more than `total`. A worker's unused share comes back when it exits and says what it used.
    """

    def __init__(self, total: int, per_worker: int, count: int):
        self.total, self.left = total, total
        self.per_worker = min(per_worker, total // max(count, 1))
        if self.per_worker < MIN_SHARE:
            raise ValueError(f"a cap of {total} model calls for the run leaves {self.per_worker} for each of {count} "
                             f"worker(s); a ticket takes about {MIN_SHARE}: raise the run's cap or start fewer")
        self._lock = threading.Lock()

    def take(self) -> int:
        """A share for one more worker, or 0 when what is left is too little to start one."""
        with self._lock:
            share = min(self.per_worker, self.left)
            if share < MIN_SHARE:
                return 0
            self.left -= share
            return share

    def give_back(self, calls: int) -> None:
        with self._lock:
            self.left += max(calls, 0)


def task_ids(worker: int, items: int) -> list[str]:
    """Worker `worker`'s task (counting from 1): `items` item ids, empty for no task."""
    return [f"w{worker:02d}-item-{i:02d}" for i in range(1, items + 1)]


class ProxyCaError(Exception):
    """The running egress proxy's CA certificate could not be obtained."""


def fetch_proxy_ca(docker_out: Callable[[list[str]], str], into: str) -> str:
    """Copy the CA certificate of the egress proxy that is actually running into `into`; return that directory.

    Workers trust only this CA (SSL_CERT_FILE), because the proxy re-signs every TLS connection. It is taken from
    the running proxy, not from the launching checkout's .run/proxy-certs: the stack may have been started from
    another checkout, and a missing or different CA fails every request through the proxy before it is sent.
    docker_out runs a docker command and returns its stdout.
    """
    names = docker_out(["docker", "ps", "--filter", f"network={NETWORK}",
                        "--filter", "label=com.docker.compose.service=proxy", "--format", "{{.Names}}"]).split()
    if not names:
        raise ProxyCaError(f"no egress proxy is running on {NETWORK}: start the stack first (make demo)")
    if len(names) > 1:
        raise ProxyCaError(f"more than one egress proxy is running on {NETWORK}: {', '.join(names)}")
    target = Path(into)
    target.mkdir(parents=True, exist_ok=True)
    cert = target / CA_FILE
    cert.unlink(missing_ok=True)  # never mount a certificate left by an earlier run
    docker_out(["docker", "cp", f"{names[0]}:{CERT}", str(cert)])
    if not cert.exists() or "BEGIN CERTIFICATE" not in cert.read_text(errors="replace"):
        raise ProxyCaError(f"the egress proxy {names[0]} has no CA certificate at {CERT}")
    return str(target)


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
        # botocore (the worker's Bedrock and STS calls) reads AWS_CA_BUNDLE, not SSL_CERT_FILE.
        env.update({"BURN_PROXY_URL": PROXY, "HTTPS_PROXY": identity, "HTTP_PROXY": identity,
                    "https_proxy": identity, "http_proxy": identity, "NO_PROXY": "127.0.0.1,localhost",
                    "no_proxy": "127.0.0.1,localhost", "SSL_CERT_FILE": CERT, "REQUESTS_CA_BUNDLE": CERT,
                    "AWS_CA_BUNDLE": CERT})
    return env


def image_tag(root: Path = REPO_ROOT) -> str:
    """`burn-swarm-worker:<hash>` of what the worker image is built from.

    Each build gets the tag of its own content, and its containers run that exact tag, so swarms started at the same
    time from different checkouts never run each other's image. The same code always gives the same tag.
    """
    digest = hashlib.sha256()
    for rel in IMAGE_INPUTS:
        base = root / rel
        files = [base] if base.is_file() else sorted(
            f for f in base.rglob("*")
            if f.is_file() and not NOT_IN_IMAGE & set(f.relative_to(base).parts) and f.suffix != ".pyc")
        for f in files:
            for part in (f.relative_to(root).as_posix().encode(), f.read_bytes()):
                digest.update(len(part).to_bytes(8, "big") + part)
    return f"{IMAGE_NAME}:{digest.hexdigest()[:12]}"


def worker_args(*, mock: bool, interval: float, model_calls: int = 0) -> list[str]:
    """The worker's own options. `burn run` passes the worker an allow-listed environment, so they go in argv."""
    if mock:  # the mock servers have no model: the scripted worker
        return ["--scripted", "--interval", str(interval)]
    return ["--interval", str(interval), "--max-model-calls", str(model_calls)]


def docker_run_argv(name: str, role: str, public: dict, env_names: list[str], *, mock: bool, image: str,
                    certs_dir: str = "", args: list[str] = ()) -> list[str]:
    """`docker run` for one worker. Environment variables are passed by name; their values are not in this list."""
    # --pull never: run only the image this launcher built; if it is gone, fail rather than fetch one by that name
    argv = ["docker", "run", "-d", "--pull", "never", "--name", name, "--label", "burn-swarm=1"]
    argv += ["--add-host", f"{HOST}:host-gateway"] if mock else ["--network", NETWORK, "-v", f"{certs_dir}:/certs:ro"]
    for env_name in env_names:
        argv += ["-e", env_name]
    # `public` is the session without its secrets: ids, role, tools. Safe to show in `ps`.
    return argv + [image, "burn", "run", "--role", role, "--adopt", json.dumps(public), "--", *WORKER, *args]


class BurnCalls:
    """Every Burn API call the launcher makes.

    The person-side calls (join tokens, reading an instance, linking a replacement) go through `api`, which carries the
    launcher's own sign-in. Joining and ending an instance go through `instances`, which sends no person
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

    def instance(self, session_id: str) -> dict:
        return self.api.request("GET", f"/instances/{session_id}")

    def replace(self, old_session: str, new_session: str) -> dict:
        return self.api.request("POST", f"/instances/{old_session}/replace", {"session_id": new_session})


@dataclass
class Worker:
    """What the launcher knows about one container: its instance, its task (empty without one) and its share of the
    run's model calls (0 for the scripted worker)."""
    agent_id: str
    session_id: str
    task: list[str]
    model_calls: int = 0


class Swarm:
    """The launcher's workers: the map of container to task, and what to do when a container exits."""

    def __init__(self, calls: BurnCalls, role: str, *, api_url: str, gateway_url: str, mock: bool, interval: float,
                 certs_dir: str, docker: Callable[[list[str], dict], object], image: str, sleep=None, log=None,
                 budget: RunBudget | None = None):
        self.calls, self.role, self.docker, self.budget = calls, role, docker, budget
        self._container = {"api_url": api_url, "gateway_url": gateway_url, "mock": mock, "interval": interval}
        self.mock, self.certs_dir, self.image = mock, certs_dir, image
        self.sleep, self.log = sleep or time.sleep, log or (lambda line: print(line, flush=True))
        self.workers: dict[str, Worker] = {}
        self.used: dict[str, int] = {}  # model calls each container said it used (its last log line)
        self._lock = threading.Lock()  # follow() handles each worker's exit on its own thread

    @property
    def names(self) -> list[str]:
        """Every container started, replacements included, so stopping the launcher removes them all."""
        with self._lock:
            return list(self.workers)

    def start(self, count: int, items: int = 0) -> list[str]:
        """Join `count` times with one join token and start one container per instance. Returns their names."""
        token = self.calls.join_token(self.role, count)
        started = []
        try:
            for k in range(1, count + 1):
                started.append(self._launch(self.calls.join(self.role, token), task_ids(k, items), None,
                                            self._share()))
        except BaseException:
            if started:  # do not leave the workers that did start running with nobody watching them
                try:
                    self.docker(["docker", "rm", "-f", *started], {})
                except BaseException:  # noqa: BLE001
                    pass
            raise
        return started

    def _launch(self, reply: dict, task: list[str], resume: dict | None = None, model_calls: int = 0) -> str:
        """Start the container for a joined instance. If it does not start, the session is ended with its own token."""
        try:
            public, secrets = split_reply(reply)
            env = container_env(public=public, secrets=secrets, task=task, resume=resume, **self._container)
            name = f"burn-swarm-{self.role}-{public['agent_id'].rsplit('-', 1)[-1]}"
            args = worker_args(mock=self.mock, interval=self._container["interval"], model_calls=model_calls)
            self.docker(docker_run_argv(name, self.role, public, sorted(env), mock=self.mock, image=self.image,
                                        certs_dir=self.certs_dir, args=args), env)
        except BaseException:
            try:
                self.calls.end(reply["session_id"], (reply.get("secrets") or {}).get("session_token", ""))
            except Exception:  # noqa: BLE001 - best effort while already failing
                pass
            raise
        with self._lock:  # started: its supervisor owns the session now
            self.workers[name] = Worker(public["agent_id"], public["session_id"], task, model_calls)
        return name

    def _share(self) -> int:
        """Model calls for one more AI worker (0: the run's cap leaves too few), or 0 for the scripted worker."""
        return self.budget.take() if self.budget and not self.mock else 0

    def observe(self, name: str, line: str) -> None:
        """A line of a worker's log: notes the model calls it says it used."""
        if match := USAGE_RE.search(line):
            with self._lock:
                self.used[name] = int(match[1])

    def _settle(self, worker: Worker, name: str) -> None:
        """A worker exited: the part of its share it said it did not use goes back to the run. Without its word,
        nothing does (it may have used it all)."""
        with self._lock:
            used = self.used.pop(name, None)
        if self.budget and worker.model_calls and used is not None:
            self.budget.give_back(worker.model_calls - min(used, worker.model_calls))

    def on_exit(self, name: str) -> str | None:
        """A worker's container exited. Returns the name of the container that replaced it, or None.

        Only a burned instance whose replacement Burn has marked pending is replaced. Nothing here retries: a step that
        fails is logged, and the burned instance stays pending for a person.
        """
        worker = self.workers.get(name)
        if worker is not None:
            self._settle(worker, name)
        if worker is None or not worker.task:
            return None  # without a task the launcher only follows logs, as it always has
        try:
            instance = self._read(worker.session_id)
        except Exception as exc:  # noqa: BLE001
            self.log(f"{worker.agent_id} exited and Burn could not be asked about it ({exc}): not restarted")
            return None
        record = instance.get("replacement") or {}
        if record.get("status") != "pending":
            self.log(f"{worker.agent_id} {_why_not(instance)}: not restarted")
            return None
        return self._replace(worker)

    def _read(self, session_id: str) -> dict:
        """The instance, read again (a few times, briefly) while it is burned and its record is not written yet."""
        for attempt in range(READS):
            instance = self.calls.instance(session_id)
            if instance.get("status") != "revoked" or instance.get("replacement") or attempt == READS - 1:
                return instance
            self.sleep(READ_GAP)
        raise AssertionError("unreachable")

    def _replace(self, old: Worker) -> str | None:
        """One fresh join with a single-use token, link it to the burned instance, start it on the same task."""
        share = self._share()
        if self.budget and not self.mock and not share:
            self.log(f"cannot replace {old.agent_id}: the run's cap of {self.budget.total} model calls is spent; "
                     f"it stays pending")
            return None
        name = self._join_and_link(old, share)
        if name is None and self.budget:  # nothing started: its share goes back to the run
            self.budget.give_back(share)
        return name

    def _join_and_link(self, old: Worker, share: int) -> str | None:
        try:
            reply = self.calls.join(self.role, self.calls.join_token(self.role, 1, ttl=REPLACEMENT_JOIN_TTL))
        except Exception as exc:  # noqa: BLE001
            self.log(f"cannot replace {old.agent_id}: no fresh instance could join ({exc}); it stays pending")
            return None
        try:
            linked = self.calls.replace(old.session_id, reply["session_id"])
        except Exception as exc:  # noqa: BLE001 - 409: no longer pending (linked elsewhere, or needs a person)
            outcome = "it is no longer pending: not restarted" if getattr(exc, "status", None) == 409 else "it stays pending"
            self.log(f"cannot replace {old.agent_id}: Burn did not link {reply['agent_id']} ({exc}); {outcome}")
            try:
                self.calls.end(reply["session_id"], (reply.get("secrets") or {}).get("session_token", ""))
            except Exception:  # noqa: BLE001
                pass
            return None
        try:
            name = self._launch(reply, old.task, linked, share)
        except BaseException as exc:  # noqa: BLE001 - docker() exits on failure; this thread must not die silently
            self.log(f"{reply['agent_id']} replaced {old.agent_id} in Burn, but its container did not start ({exc})")
            return None
        self.log(f"replaced {old.agent_id} with {reply['agent_id']}: "
                 f"{len(linked['done'])} done, {len(linked['quarantined'])} quarantined")
        return name


def _why_not(instance: dict) -> str:
    """Why an exited worker is not replaced, for the launcher's log."""
    status, record = instance.get("status"), instance.get("replacement") or {}
    if record.get("status") == "needs_person":
        return "was burned and needs a person: its task has been replaced the most times allowed"
    if record.get("status") == "linked":
        return f"was burned and already replaced by {instance.get('replaced_by')}"
    if status == "revoked":
        return ("was burned and Burn has no replacement record (replace on burn is off for the role, "
                "or the whole group was burned)")
    if status == "exited":
        return "exited"
    return f"stopped but is still active in Burn (status {status})"


def start_workers(api: Api, instances: InstanceApi, role: str, count: int, *, api_url: str, gateway_url: str,
                  mock: bool, interval: float, certs_dir: str, docker: Callable[[list[str], dict], object],
                  image: str, items: int = 0, sleep=None, log=None, budget: RunBudget | None = None) -> Swarm:
    """Join `count` times with one join token and start one container per instance, each with a task of `items` items
    and, unless `mock`, its share of `budget` (the run's model calls).

    A session whose container could not be started is ended, so a failure does not leave instances dangling.
    """
    if budget is None and not mock:
        budget = RunBudget(DEFAULT_RUN_CALLS, DEFAULT_WORKER_CALLS, count)
    swarm = Swarm(BurnCalls(api, instances, sleep=sleep), role, api_url=api_url, gateway_url=gateway_url, mock=mock,
                  interval=interval, certs_dir=certs_dir, docker=docker, image=image, sleep=sleep, log=log,
                  budget=budget)
    swarm.start(count, items)
    return swarm
