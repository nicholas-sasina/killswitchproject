import stat
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from swarm_compose import build_compose, swarm_env  # noqa: E402
from run_swarm import write_private  # noqa: E402


def test_real_mode_workers_join_only_the_internal_network_and_run_under_burn_run():
    svc = build_compose("worker")["services"]["worker"]
    assert svc["networks"] == ["agents_internal"] and svc["depends_on"] == ["proxy"]
    assert svc["command"][:5] == ["burn", "run", "--role", "worker", "--"]
    assert svc["environment"]["BURN_PROXY_URL"] == "http://proxy:8080"
    assert "ports" not in svc


def test_no_credential_value_is_in_the_compose_file():
    text = yaml.safe_dump(build_compose("worker"))
    for name in ("BURN_JOIN_TOKEN", "BURN_DEMO_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert name not in text  # the supervisor's settings live in .run/swarm.env only


def test_mock_mode_uses_the_fake_credential_source_and_no_proxy():
    compose = build_compose("worker", mock=True)
    svc = compose["services"]["worker"]
    assert svc["environment"]["BURN_CREDENTIAL_SOURCE"] == "fake" and "depends_on" not in svc
    assert compose["name"] == "burn-demo"


def test_interval_reaches_the_worker():
    assert build_compose("w", interval=0.5)["services"]["worker"]["environment"]["BURN_WORKER_INTERVAL"] == "0.5"


def test_mock_env_points_containers_at_the_host():
    env = swarm_env(api_url="http://localhost:8700", token="t", join_token="j", gateway_url="http://127.0.0.1:8701/mcp", mock=True)
    assert env["BURN_API_URL"] == "http://host.docker.internal:8700"
    assert env["BURN_GATEWAY_URL"] == "http://host.docker.internal:8701/mcp"
    assert env["BURN_MOCK_BADGE_URL"] == "http://host.docker.internal:8702"
    assert swarm_env(api_url="https://x", token="t", join_token="j", gateway_url="https://g", mock=False)["BURN_API_URL"] == "https://x"


def test_swarm_env_file_is_private(tmp_path):
    path = tmp_path / "run" / "swarm.env"
    write_private(path, "A=1\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
