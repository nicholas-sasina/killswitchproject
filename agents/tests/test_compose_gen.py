import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from compose_gen import build_compose  # noqa: E402

SCENARIOS = sorted((Path(__file__).resolve().parents[1] / "scenarios").glob("*.yaml"))


def test_every_scenario_generates_internal_only_agent_services():
    for path in SCENARIOS:
        scenario = yaml.safe_load(path.read_text())
        sessions = {a["id"]: f"sess-{i}" for i, a in enumerate(scenario["agents"])}
        compose = build_compose(scenario, path.name, sessions, step=False)
        assert set(compose["services"]) == set(sessions)
        for agent_id, svc in compose["services"].items():
            assert svc["networks"] == ["agents_internal"]
            assert svc["environment"]["HTTPS_PROXY"] == f"http://{agent_id}:{sessions[agent_id]}@proxy:8080"
            assert svc["environment"]["AWS_CA_BUNDLE"] == "/certs/mitmproxy-ca-cert.pem"
            assert "BURN_STEP_FILE" not in svc["environment"]


def test_step_mode_gates_only_the_ticket_reader():
    scenario = yaml.safe_load(SCENARIOS[0].read_text())
    sessions = {a["id"]: "s" for a in scenario["agents"]}
    services = build_compose(scenario, SCENARIOS[0].name, sessions, step=True)["services"]
    gated = [i for i, s in services.items() if "BURN_STEP_FILE" in s["environment"]]
    assert gated == [a["id"] for a in scenario["agents"] if "list_tickets" in a["tools"]]


def test_agents_trust_the_proxy_ca_from_the_shared_host_directory():
    scenario = yaml.safe_load(SCENARIOS[0].read_text())
    sessions = {a["id"]: "s" for a in scenario["agents"]}
    for svc in build_compose(scenario, SCENARIOS[0].name, sessions, step=False)["services"].values():
        assert "../.run/proxy-certs:/certs:ro" in svc["volumes"]
