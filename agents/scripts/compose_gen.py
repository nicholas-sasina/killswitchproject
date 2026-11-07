"""Generate one compose service per scenario agent.

The agent list lives in the scenario file, so a new scenario needs no code
change. Agents join ONLY the internal network; their one way out is the proxy.
"""
CERT = "/certs/mitmproxy-ca-cert.pem"


def agent_service(agent_id: str, session_id: str, scenario_file: str, step: bool, ticket_agent: bool) -> dict:
    proxy = f"http://{agent_id}:{session_id}@proxy:8080"
    env = {
        "AGENT_ID": agent_id,
        "BURN_SCENARIO_FILE": f"/scenarios/{scenario_file}",
        "HTTPS_PROXY": proxy, "HTTP_PROXY": proxy,
        "https_proxy": proxy, "http_proxy": proxy,
        "SSL_CERT_FILE": CERT, "REQUESTS_CA_BUNDLE": CERT, "AWS_CA_BUNDLE": CERT,
    }
    if step and ticket_agent:
        env["BURN_STEP_FILE"] = f"/step/{agent_id}.go"
    return {
        "build": ".",
        "env_file": [f".run/{agent_id}.env"],
        "environment": env,
        "networks": ["agents_internal"],
        "depends_on": ["proxy"],
        "volumes": ["./scenarios:/scenarios:ro", "../.run/proxy-certs:/certs:ro", "./.run/step:/step"],
        "restart": "no",
    }


def build_compose(scenario: dict, scenario_file: str, sessions: dict[str, str], step: bool) -> dict:
    services = {}
    for agent in scenario["agents"]:
        services[agent["id"]] = agent_service(
            agent["id"], sessions[agent["id"]], scenario_file, step, "list_tickets" in agent["tools"])
    return {"services": services}
