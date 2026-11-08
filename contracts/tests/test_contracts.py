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


def test_session_tokens_are_recognized_and_caught_by_gitleaks():
    import secrets
    import tomllib
    token = names.SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)
    assert names.is_session_token(token)
    for bad in [None, "", "bst_short", token[:-1], token + "x", "bjt_" + token[4:], token + "\n"]:
        assert not names.is_session_token(bad)
    rules = tomllib.loads((ROOT.parent / ".gitleaks.toml").read_text())["rules"]
    rule = next(r for r in rules if r["id"] == "burn-session-token")
    assert re.search(rule["regex"], f"Authorization: Bearer {token}")


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


WORKER = names.instance_agent_id("worker", "3fa91c")  # agent-worker-3fa91c: one instance of role worker


def test_canary_used_event_with_instance_is_schema_valid():
    ref = names.canary_secret_ref(WORKER, "sess_0123456789abcdef")
    event = events.make_event(
        agent_id=WORKER, source="provider", action="canary_used", session_id="sess_0123456789abcdef",
        role="worker", secret_ref=ref, secret_type="other", owner="agent", owner_agent_id=WORKER,
        detail="canary presented at the provider",
    )
    jsonschema.validate(event, EVENT_SCHEMA)
    with pytest.raises(TypeError):  # the instance is the session: there is no separate instance_id
        events.make_event(agent_id=WORKER, source="provider", action="canary_used", instance_id="x")


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
    assert event["flag_level"] is None
    jsonschema.validate(event, EVENT_SCHEMA)


def test_canary_secret_ref_is_per_session_deterministic_and_valid():
    a = names.canary_secret_ref(WORKER, "sess_aaaa")
    b = names.canary_secret_ref(WORKER, "sess_bbbb")
    other = names.canary_secret_ref(names.instance_agent_id("worker", "0b7c2e"), "sess_aaaa")
    assert a == names.canary_secret_ref(WORKER, "sess_aaaa")
    assert len({a, b, other}) == 3  # per session, and per instance of the same role
    assert a.startswith(f"burn/{WORKER}/canary_") and len(a.rsplit("_", 1)[1]) == 24
    assert names.owner_of(a) == WORKER
    assert names.is_canary_ref(a) and not names.is_canary_ref(f"burn/{WORKER}/api_key")
    # stays inside the event schema's secret_ref grammar even for long instance-style ids
    long_ref = names.canary_secret_ref("agent-" + "w" * 40, "i-" + "x" * 120)
    jsonschema.validate(events.make_event(agent_id="system", source="provider", action="canary_used",
                                          secret_ref=long_ref), EVENT_SCHEMA)
    for bad in ("", "has space", "x" * 129, "sess/../x"):
        with pytest.raises(ValueError):
            names.canary_secret_ref(WORKER, bad)
    with pytest.raises(ValueError):
        names.canary_secret_ref("shared", "sess_aaaa")


def test_roles_can_turn_off_canaries_and_auto_burn():
    schema = json.loads((ROOT / "burn.schema.json").read_text())
    role = {"name": "worker", "tools": ["read_config"], "canaries": True, "auto_burn": False}
    jsonschema.validate({"version": 1, "roles": [role]}, schema)
    props = schema["$defs"]["role"]["properties"]
    assert props["canaries"]["default"] is True and props["auto_burn"]["default"] is True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"version": 1, "roles": [role | {"auto_burn": "no"}]}, schema)
    api = yaml.safe_load((ROOT / "burn-api.openapi.yaml").read_text())
    fields = api["components"]["schemas"]["RoleFields"]["properties"]
    assert fields["canaries"]["default"] is True and fields["auto_burn"]["default"] is True


def test_dismissed_event_and_flag_routes():
    event = events.make_event(agent_id="system", source="burn", action="dismissed",
                              secret_ref="burn/shared/crm_db_url", flag_level="attempted",
                              detail="dismissed by sec-team: test fixture, not a real database")
    jsonschema.validate(event, EVENT_SCHEMA)
    for spec in ("burn-api.openapi.yaml", "minter.openapi.yaml"):
        doc = yaml.safe_load((ROOT / spec).read_text())
        assert {"/flags", "/flags/resolve"} <= set(doc["paths"])
        resolution = doc["components"]["schemas"]["FlagResolution"]
        assert resolution["properties"]["action"]["enum"] == ["rotate", "dismiss"]
        assert "reason" in resolution["required"]
        assert "instance_count" in doc["components"]["schemas"]["Flag"]["required"]


def test_eval_role_names_are_reserved_for_the_eval_harness():
    from burn_contracts import names
    for reserved in ("eval", "eval-7", "eval-"):
        assert names.is_reserved_role_name(reserved)
    for fine in ("evaluator", "my-eval", "worker", "eva"):
        assert not names.is_reserved_role_name(fine)


def test_tool_mirrors_match_mcp_tools_json():
    from burn_contracts import tools
    doc = json.loads((ROOT / "mcp-tools.json").read_text())
    by_name = {t["name"]: t for t in doc["tools"]}
    assert {n: t["resource_arg"] for n, t in by_name.items() if t["resource_arg"]} == tools.RESOURCE_ARG
    assert {n: tuple(t["accepts_secret_refs"]) for n, t in by_name.items() if t["accepts_secret_refs"]} == \
        tools.ACCEPTS_SECRET_REFS
    for name, tool in by_name.items():  # a resource or resolvable arg must be a real input
        props = tool["inputSchema"]["properties"]
        assert tool["resource_arg"] in (None, *props)
        assert set(tool["accepts_secret_refs"]) <= set(props)
    assert not by_name["send_message"]["accepts_secret_refs"] and not by_name["notify"]["accepts_secret_refs"]


def test_source_ref_and_resolvable_args():
    from burn_contracts import tools
    assert tools.source_ref("read_ticket", {"ticket_id": "T-1001"}) == "read_ticket:T-1001"
    assert tools.source_ref("read_config", {"path": "config/app.env"}) == "read_config:config/app.env"
    assert tools.source_ref("list_tickets", {}) == "list_tickets"
    assert tools.source_ref("read_ticket", {}) == "read_ticket"
    assert tools.source_ref("read_ticket", {"ticket_id": 1001}) == "read_ticket:1001"
    assert tools.source_ref("read_config", {"path": "x" * 200}) == "read_config:" + "x" * 200


def test_source_refs_never_collide():
    from burn_contracts import tools
    import hashlib
    hashed = tools.source_ref("read_config", {"path": "a\nb"})
    assert hashed == "read_config:sha256:" + hashlib.sha256(b"a\nb").hexdigest()
    pairs = [("a\nb", "ab"), ("x" * 201, "x" * 202), ("y" * 300 + "1", "y" * 300 + "2"),
             ("sha256:" + "0" * 64, "plain")]
    for one, other in pairs:
        assert tools.source_ref("read_config", {"path": one}) != tools.source_ref("read_config", {"path": other})
    literal = "sha256:" + hashlib.sha256(b"a\nb").hexdigest()  # a resource named like a hash is hashed too
    assert tools.source_ref("read_config", {"path": literal}) != hashed
    assert all(len(tools.source_ref("read_config", {"path": p})) <= len("read_config:") + 200 for p, _ in pairs)
    assert tools.resolvable_args("send_message") == () and tools.resolvable_args("notify") == ()


def test_reference_tokens():
    from burn_contracts import references
    tok = references.token("burn/shared/crm_db_url")
    assert tok == "{{burn:ref:burn/shared/crm_db_url}}"
    assert references.REF_RE.fullmatch(tok)["secret_ref"] == "burn/shared/crm_db_url"
    with pytest.raises(ValueError):
        references.token("arn:aws:secretsmanager:whatever")
    assert references.mode({}) == "replace" and references.mode({"references": "report"}) == "report"
    assert references.mode({"references": "bogus"}) == "replace"


def test_references_role_field_ledger_fields_and_by_source_route():
    schema = json.loads((ROOT / "burn.schema.json").read_text())
    prop = schema["$defs"]["role"]["properties"]["references"]
    assert prop["enum"] == ["replace", "report", "off"] and prop["default"] == "replace"
    api = yaml.safe_load((ROOT / "burn-api.openapi.yaml").read_text())
    assert api["components"]["schemas"]["RoleFields"]["properties"]["references"]["default"] == "replace"
    entry = api["components"]["schemas"]["LedgerEntry"]["properties"]
    assert entry["exposure"]["enum"] == ["seen", "referenced"] and "source_ref" in entry
    assert "/burn/by-source" in api["paths"]
    assert names.FINGERPRINTS_VERSION_KEY == "_version" and len(names.FINGERPRINTS_VERSION_KEY) != 64
    assert names.EXPOSURES_BY_SOURCE_INDEX == "by_source"


@pytest.mark.parametrize("spec", ["burn-api.openapi.yaml", "leaktest.openapi.yaml"])
def test_specs_have_no_duplicate_yaml_keys(spec):
    """A repeated key makes YAML keep only the last one, silently dropping the first (e.g. a path's GET)."""
    class Strict(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        keys = [loader.construct_object(k) for k, _ in node.value]
        dupes = {k for k in keys if keys.count(k) > 1}
        assert not dupes, f"{spec}: duplicate keys {sorted(map(str, dupes))}"
        return loader.construct_mapping(node)

    Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    yaml.load((ROOT / spec).read_text(), Loader=Strict)
