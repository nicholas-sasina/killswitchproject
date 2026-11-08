"""The compose override that runs N swarm workers, each under `burn run`.

Same shape as the scenario demo (see compose_gen.py): workers join only the internal network, so their one way
out is the egress proxy. The proxy is a convenience for well-behaved clients; the network rule is the containment.
Only the mock form is used today (run_swarm.py refuses the other); the stack form is kept for when it is enabled.
"""
CERT = "/certs/mitmproxy-ca-cert.pem"
PROXY = "http://proxy:8080"
HOST = "host.docker.internal"
# Variables the supervising `burn run` reads. They are removed from the worker's own environment.
SUPERVISOR_VARS = ("BURN_API_URL", "BURN_DEMO_TOKEN", "BURN_JOIN_TOKEN", "BURN_GATEWAY_URL")


def command(role: str) -> list[str]:
    return ["burn", "run", "--role", role, "--", "python", "-m", "burn_agents.swarm_worker"]


def worker_service(role: str, *, mock: bool, interval: float) -> dict:
    service = {
        "build": {"context": "..", "dockerfile": "agents/Dockerfile.swarm"},
        "env_file": [".run/swarm.env"],
        "command": command(role),
        "environment": {"BURN_WORKER_INTERVAL": str(interval)},
        "restart": "no",
    }
    if mock:
        # Development against the mock servers on the host: no proxy, no internal network.
        service["environment"]["BURN_CREDENTIAL_SOURCE"] = "fake"
        service["extra_hosts"] = [f"{HOST}:host-gateway"]
        return service
    service.update({
        "networks": ["agents_internal"],
        "depends_on": ["proxy"],
        "volumes": ["../.run/proxy-certs:/certs:ro"],
    })
    service["environment"].update({
        "BURN_PROXY_URL": PROXY,
        # The supervisor itself reaches the Burn API, Gateway and Secrets Manager through the proxy too.
        "HTTPS_PROXY": PROXY, "HTTP_PROXY": PROXY, "https_proxy": PROXY, "http_proxy": PROXY,
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "SSL_CERT_FILE": CERT, "REQUESTS_CA_BUNDLE": CERT, "AWS_CA_BUNDLE": CERT,
    })
    return service


def build_compose(role: str, *, mock: bool = False, interval: float = 3.0) -> dict:
    """A compose file with one `worker` service; scale it with `up --scale worker=N`."""
    compose = {"services": {"worker": worker_service(role, mock=mock, interval=interval)}}
    if mock:
        compose["name"] = "burn-demo"
    return compose


def swarm_env(*, api_url: str, token: str, join_token: str, gateway_url: str, mock: bool) -> dict:
    """Contents of .run/swarm.env (mode 600, gitignored): the supervisor's settings, not the workers'."""
    env = {"BURN_API_URL": api_url, "BURN_DEMO_TOKEN": token, "BURN_JOIN_TOKEN": join_token,
           "BURN_GATEWAY_URL": gateway_url}
    if mock:
        env["BURN_MOCK_BADGE_URL"] = f"http://{HOST}:8702"
        env = {k: v.replace("localhost", HOST).replace("127.0.0.1", HOST) if k.endswith("_URL") else v
               for k, v in env.items()}
    return env
