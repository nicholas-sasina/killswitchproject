"""Scenario items match the burn-scenario layout; env files are private."""
import os
import stat
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from load_scenario import scenario_items  # noqa: E402
from run_demo import write_env_file  # noqa: E402

SCENARIO = next((Path(__file__).resolve().parents[1] / "scenarios").glob("*.yaml"))


def test_scenario_items_cover_every_object():
    s = yaml.safe_load(SCENARIO.read_text())
    items = {i["pk"]: i["data"] for i in scenario_items(s)}
    assert items["meta"]["agent_ids"] == [a["id"] for a in s["agents"]]
    assert all(f"agent#{a['id']}" in items for a in s["agents"])
    assert all(f"ticket#{t['id']}" in items for t in s["tickets"])
    assert all(f"file#{f['path']}" in items for f in s["files"])
    assert all(f"customer#{c['id']}" in items for c in s["customers"])


def test_env_file_is_owner_only(tmp_path):
    path = tmp_path / "a.env"
    write_env_file(path, {"A": "1"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert path.read_text() == "A=1\n"
