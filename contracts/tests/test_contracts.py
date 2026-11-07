"""Contract tests: helpers match the schemas, and the schemas/specs parse."""
import json
import os
import pathlib
import time

import jsonschema
import pytest
import yaml

from burn_contracts import events, fingerprint, ids, names

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVENT_SCHEMA = json.loads((ROOT / "ledger-event.schema.json").read_text())
SCENARIO_SCHEMA = json.loads((ROOT / "scenario.schema.json").read_text())


def test_event_ids_are_well_formed_and_time_sortable():
    first = ids.new_event_id(now_ms=1_700_000_000_000)
    second = ids.new_event_id(now_ms=1_700_000_000_001)
    assert first.startswith("evt_") and len(first) == 30
    assert first < second


def test_event_ids_sort_by_wall_clock():
    a = ids.new_event_id()
    time.sleep(0.002)
    b = ids.new_event_id()
    assert a < b


def test_make_event_is_schema_valid():
    key = os.urandom(32)
    fp = fingerprint.fingerprint("not-a-real-secret-" + os.urandom(4).hex(), key)
    event = events.make_event(
        agent_id="agent-reader", source="tool_response", action="recorded",
        tool="read_config", secret_ref="burn/agent-reader/api_key", fingerprint=fp,
        secret_type="api_key", owner="agent", owner_agent_id="agent-reader",
        detail="api key seen in read_config output",
    )
    jsonschema.validate(event, EVENT_SCHEMA)


def test_minimal_event_is_schema_valid():
    jsonschema.validate(events.make_event(agent_id="system", source="burn", action="revoked"), EVENT_SCHEMA)


@pytest.mark.parametrize("bad", [
    {"source": "nope"}, {"action": "nope"}, {"secret_type": "password"},
    {"secret_ref": "arn:aws:secretsmanager:whatever"}, {"fingerprint": "abc"},
])
def test_make_event_rejects_bad_values(bad):
    kwargs = {"agent_id": "agent-reader", "source": "egress", "action": "blocked"} | bad
    with pytest.raises(ValueError):
        events.make_event(**kwargs)


def test_make_event_rejects_unknown_fields():
    with pytest.raises(TypeError):
        events.make_event(agent_id="agent-reader", source="egress", action="blocked", raw_value="x")


def test_python_enums_match_json_schema():
    props = EVENT_SCHEMA["properties"]
    assert set(props["source"]["enum"]) == events.SOURCES
    assert set(props["action"]["enum"]) == events.ACTIONS
    assert set(props["secret_type"]["enum"]) - {None} == events.SECRET_TYPES
    assert set(props["owner"]["enum"]) - {None} == events.OWNERS
    assert set(props) == {"event_id", "ts", "agent_id", "source", "action", *events.OPTIONAL_FIELDS}


def test_fingerprints_are_keyed_and_deterministic():
    value = "mpk_live_" + os.urandom(16).hex()
    k1, k2 = os.urandom(32), os.urandom(32)
    assert fingerprint.fingerprint(value, k1) == fingerprint.fingerprint(value, k1)
    assert fingerprint.fingerprint(value, k1) != fingerprint.fingerprint(value, k2)


def test_shingles_cover_every_window_case_insensitively():
    key = os.urandom(32)
    body = os.urandom(16).hex().upper()  # 32 chars
    fps = fingerprint.shingle_fingerprints(body, key)
    assert len(fps) == 32 - fingerprint.SHINGLE_K + 1
    window = body[5 : 5 + fingerprint.SHINGLE_K].lower()
    assert (5, fingerprint.shingle_fingerprint(window, key)) in fps
    # shingle and exact fingerprints never collide for the same text
    assert fingerprint.shingle_fingerprint(window, key) != fingerprint.fingerprint(window, key)


def test_names_helpers():
    assert names.agent_secret_ref("agent-reader", "api_key") == "burn/agent-reader/api_key"
    assert names.owner_of("burn/shared/crm_db_url") == "shared"
    with pytest.raises(ValueError):
        names.agent_secret_ref("shared", "api_key")


def test_example_scenario_is_valid():
    scenario = yaml.safe_load((ROOT / "examples" / "minimal-scenario.yaml").read_text())
    jsonschema.validate(scenario, SCENARIO_SCHEMA)


def test_scenario_tools_exist_in_mcp_tools():
    tools = {t["name"] for t in json.loads((ROOT / "mcp-tools.json").read_text())["tools"]}
    scenario = yaml.safe_load((ROOT / "examples" / "minimal-scenario.yaml").read_text())
    for agent in scenario["agents"]:
        assert set(agent["tools"]) <= tools


@pytest.mark.parametrize("spec", ["burn-api.openapi.yaml", "minter.openapi.yaml", "leaktest.openapi.yaml"])
def test_openapi_files_parse(spec):
    doc = yaml.safe_load((ROOT / spec).read_text())
    assert doc["openapi"].startswith("3.") and doc["paths"]
