"""Contract tests: helpers match the schemas, and the schemas/specs parse."""
import json
import os
import pathlib
import re
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


# --- roles, instances and join tokens ---------------------------------------------------------

BURN_YAML_SCHEMA = json.loads((ROOT / "burn.schema.json").read_text())


def _new_join_token() -> str:
    import secrets
    return f"{names.JOIN_TOKEN_PREFIX}{secrets.token_hex(8)}.{secrets.token_urlsafe(32)}"


def test_role_names_and_instance_agent_ids():
    assert names.is_role_name("worker") and names.is_role_name("a") and names.is_role_name("3d-render")
    for bad in ["", "Worker", "-worker", "w" * 33, "work_er"]:
        assert not names.is_role_name(bad)
    longest = names.instance_agent_id("w" * 32, "3fa91c")
    assert names.is_agent_id(longest)
    assert names.instance_agent_id("worker", "3fa91c") == "agent-worker-3fa91c"
    with pytest.raises(ValueError):
        names.instance_agent_id("worker", "XYZ")


def test_scenario_agents_map_to_singleton_roles():
    assert names.scenario_role_name("agent-reader") == "reader"
    with pytest.raises(ValueError):
        names.scenario_role_name("system")


def test_join_tokens_parse_and_are_caught_by_gitleaks():
    import tomllib
    token = _new_join_token()
    token_id, secret = names.parse_join_token(token)
    assert token == f"bjt_{token_id}.{secret}"
    assert names.parse_join_token("bjt_nothex.x") is None and names.parse_join_token(None) is None
    rules = tomllib.loads((ROOT.parent / ".gitleaks.toml").read_text())["rules"]
    rule = next(r for r in rules if r["id"] == "burn-join-token")
    assert re.search(rule["regex"], f"export BURN_JOIN_TOKEN={token}")


def test_instance_events_are_schema_valid():
    started = events.make_event(
        agent_id="agent-worker-3fa91c", source="instance", action="started",
        session_id="sess-1", role="worker", parent_session="sess-0",
    )
    jsonschema.validate(started, EVENT_SCHEMA)
    jsonschema.validate(events.make_event(agent_id="system", source="gateway", action="unregistered"), EVENT_SCHEMA)
    with pytest.raises(ValueError):
        events.make_event(agent_id="agent-worker-3fa91c", source="instance", action="started", role="Bad Role")


def test_example_burn_yaml_is_valid_and_consistent():
    doc = yaml.safe_load((ROOT / "examples" / "burn.yaml").read_text())
    jsonschema.validate(doc, BURN_YAML_SCHEMA)
    tools = {t["name"] for t in json.loads((ROOT / "mcp-tools.json").read_text())["tools"]}
    roles = {r["name"]: r for r in doc["roles"]}
    for role in roles.values():
        assert set(role["tools"]) <= tools
        assert set(role.get("may_message", [])) <= set(roles)
        assert set(role.get("may_spawn", [])) <= set(roles)


def test_role_name_pattern_is_the_same_everywhere():
    api = yaml.safe_load((ROOT / "burn-api.openapi.yaml").read_text())
    patterns = {
        names.ROLE_NAME_RE.pattern,
        events._ROLE_RE.pattern,
        BURN_YAML_SCHEMA["$defs"]["roleName"]["pattern"],
        api["components"]["schemas"]["RoleName"]["pattern"],
        EVENT_SCHEMA["properties"]["role"]["pattern"],
    }
    assert len(patterns) == 1, patterns


def test_burn_api_refs_resolve():
    api = yaml.safe_load((ROOT / "burn-api.openapi.yaml").read_text())
    text = (ROOT / "burn-api.openapi.yaml").read_text()
    for ref in set(re.findall(r"#/components/schemas/(\w+)", text)):
        assert ref in api["components"]["schemas"], ref
    for path in ["/roles", "/roles/{name}", "/roles/{name}/tokens", "/tokens/{token_id}", "/instances", "/burns"]:
        assert path in api["paths"], path
def test_canary_used_event_with_instance_is_schema_valid():
    ref = names.canary_secret_ref("agent-worker", "sess_0123456789abcdef")
    event = events.make_event(
        agent_id="agent-worker", source="provider", action="canary_used", session_id="sess_0123456789abcdef",
        instance_id="inst-07", secret_ref=ref, secret_type="other", owner="agent", owner_agent_id="agent-worker",
        detail="canary presented at the provider",
    )
    jsonschema.validate(event, EVENT_SCHEMA)


def test_flag_levels_are_ordered_validated_and_match_schema():
    assert events.FLAG_LEVELS == ("seen", "attempted", "reported")
    assert set(EVENT_SCHEMA["properties"]["flag_level"]["enum"]) - {None} == set(events.FLAG_LEVELS)
    flagged = events.make_event(agent_id="agent-a", source="burn", action="flagged",
                                secret_ref="burn/shared/crm_db_url", flag_level="attempted")
    jsonschema.validate(flagged, EVENT_SCHEMA)
    with pytest.raises(ValueError):
        events.make_event(agent_id="agent-a", source="burn", action="flagged", flag_level="critical")


def test_events_without_new_fields_stay_valid():
    event = events.make_event(agent_id="agent-a", source="burn", action="revoked")
    assert event["instance_id"] is None and event["flag_level"] is None
    jsonschema.validate(event, EVENT_SCHEMA)


def test_canary_secret_ref_is_per_session_deterministic_and_valid():
    a = names.canary_secret_ref("agent-worker", "sess_aaaa")
    b = names.canary_secret_ref("agent-worker", "sess_bbbb")
    assert a == names.canary_secret_ref("agent-worker", "sess_aaaa")
    assert a != b and a.startswith("burn/agent-worker/canary_") and len(a.rsplit("_", 1)[1]) == 24
    assert names.owner_of(a) == "agent-worker"
    assert names.is_canary_ref(a) and not names.is_canary_ref("burn/agent-worker/api_key")
    # stays inside the event schema's secret_ref grammar even for long instance-style ids
    long_ref = names.canary_secret_ref("agent-" + "w" * 40, "i-" + "x" * 120)
    jsonschema.validate(events.make_event(agent_id="system", source="provider", action="canary_used",
                                          secret_ref=long_ref), EVENT_SCHEMA)
    for bad in ("", "has space", "x" * 129, "sess/../x"):
        with pytest.raises(ValueError):
            names.canary_secret_ref("agent-worker", bad)
    with pytest.raises(ValueError):
        names.canary_secret_ref("shared", "sess_aaaa")
