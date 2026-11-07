"""Every scenario file must satisfy the shared schema and be self-consistent."""
import json
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import validate

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts" / "scenario.schema.json").read_text())
TOOLS = {t["name"] for t in json.loads((ROOT / "contracts" / "mcp-tools.json").read_text())["tools"]}
FILES = sorted((ROOT / "agents" / "scenarios").glob("*.yaml"))
PLACEHOLDER = re.compile(r"\{\{cred:([a-z0-9-]+):([a-z_.]+)\}\}")


def load(path):
    return yaml.safe_load(path.read_text())


def test_at_least_two_scenarios():
    assert len(FILES) >= 2


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_valid_against_schema(path):
    validate(load(path), SCHEMA)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_graph_and_placeholders_are_consistent(path):
    s = load(path)
    ids = {a["id"] for a in s["agents"]}
    shared = {c["name"] for c in s.get("shared_credentials", [])}
    for a in s["agents"]:
        assert set(a["tools"]) <= TOOLS
        assert set(a.get("send_to", [])) <= ids
        assert ("send_message" in a["tools"]) == bool(a.get("send_to"))
    text = yaml.dump([s.get("files", []), s.get("customers", [])])
    for owner, what in PLACEHOLDER.findall(text):
        if owner == "shared":
            assert what in shared
        else:
            assert owner in ids


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_reader_files_hold_own_and_shared_credentials(path):
    s = load(path)
    reader = next(a for a in s["agents"] if "list_tickets" in a["tools"])
    files = "\n".join(f["content"] for f in s["files"])
    for what in ("api_key", "aws_key.access_key_id", "aws_key.secret_access_key"):
        assert f"{{{{cred:{reader['id']}:{what}}}}}" in files
    assert "{{cred:shared:" in files
    assert "{{cred:shared:" in "\n".join(c.get("notes", "") for c in s["customers"])


def test_scenarios_share_no_agent_ids():
    a, b = (set(x["id"] for x in load(p)["agents"]) for p in FILES[:2])
    assert not a & b


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_every_agent_gets_its_own_aws_key(path):
    # The model is called with the agent's own key, so every agent needs one.
    assert all("aws_key" in a["credentials"] for a in load(path)["agents"])
