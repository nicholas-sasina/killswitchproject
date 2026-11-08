"""Contract tests: helpers match the schemas, and the schemas/specs parse."""
import json
import os
import pathlib
import re
import time
from decimal import Decimal

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


def test_a_stored_event_keeps_every_field_and_gains_only_a_ttl():
    event = events.make_event(agent_id="agent-reader", source="egress", action="blocked",
                              secret_ref="burn/agent-reader/api_key", secret_type="api_key")
    before = time.time()
    row = events.for_storage(event)
    assert set(row) == set(event) | {events.TTL_ATTRIBUTE}
    assert {k: row[k] for k in event} == event and row["flag_level"] is None  # unused fields stay, as NULLs
    ttl = row[events.TTL_ATTRIBUTE]
    days = events.EVENT_RETENTION_DAYS * 86400
    assert type(ttl) is int and int(before) + days <= ttl <= time.time() + days
    assert events.TTL_ATTRIBUTE not in event  # the event itself is untouched


def test_a_stored_event_reads_back_as_the_event_it_was():
    event = events.make_event(agent_id="agent-reader", source="burn", action="flagged",
                              secret_ref="burn/shared/crm_db_url", owner="shared", flag_level="seen")
    assert events.from_storage(events.for_storage(event)) == event
    jsonschema.validate(events.from_storage(events.for_storage(event)), EVENT_SCHEMA)
    # A row missing its unused fields (an older contract, or a writer that left them out), with the TTL
    # as DynamoDB returns a number: the fields come back as None and the TTL is gone.
    row = {k: v for k, v in events.for_storage(event).items() if v is not None}
    row[events.TTL_ATTRIBUTE] = Decimal(row[events.TTL_ATTRIBUTE])
    assert events.from_storage(row) == event


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


@pytest.mark.parametrize("spec", ["burn-api.openapi.yaml", "leaktest.openapi.yaml", "minter.openapi.yaml"])
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


# --- sign-in -----------------------------------------------------------------------------------

def _api():
    return yaml.safe_load((ROOT / "burn-api.openapi.yaml").read_text())


def _operations(api):
    for path, item in api["paths"].items():
        for method, op in item.items():
            if method in ("get", "post", "put", "delete"):
                yield path, method, op


def _schemes(api, op):
    """Names of the security schemes an operation accepts (its own list, else the global one)."""
    return {name for requirement in op.get("security", api["security"]) for name in requirement}


def test_every_security_requirement_names_a_defined_scheme():
    api = _api()
    defined = set(api["components"]["securitySchemes"])
    assert defined == {"personBearer", "instanceBearer", "joinToken", "adminHeader"}
    for path, method, op in _operations(api):
        assert _schemes(api, op) <= defined, (method, path)


def test_people_cover_every_route_except_the_instance_routes():
    api = _api()
    instance_routes = {("/instances", "post"), ("/instances/{session_id}/heartbeat", "post"),
                       ("/instances/{session_id}/exit", "post")}
    for path, method, op in _operations(api):
        schemes = _schemes(api, op)
        if (path, method) in instance_routes:
            assert "personBearer" not in schemes and "instanceBearer" in schemes, (method, path)
        else:
            assert "personBearer" in schemes and not schemes & {"instanceBearer", "joinToken"}, (method, path)


def test_a_join_token_is_accepted_only_where_a_role_instance_starts():
    api = _api()
    starts = {(path, method) for path, method, op in _operations(api) if "joinToken" in _schemes(api, op)}
    assert starts == {("/instances", "post")}  # every form of POST /sessions needs a person


def test_the_exempt_routes_are_exactly_the_three_instance_routes():
    api = _api()
    exempt = {(path, method) for path, method, op in _operations(api) if "personBearer" not in _schemes(api, op)}
    assert exempt == {("/instances", "post"), ("/instances/{session_id}/heartbeat", "post"),
                      ("/instances/{session_id}/exit", "post")}
    description = api["info"]["description"]
    assert "Every form of `POST /sessions` now needs a person" in description
    assert "BURN_PEOPLE_AUTH=1" in description and "works as before" in description  # not true until switched on
    assert "OPTIONS" in description
    assert "personBearer" not in api["paths"]["/instances"]["post"]["security"][0]


def test_a_missing_or_invalid_person_token_is_a_documented_401():
    api = _api()
    for path, method, op in _operations(api):
        assert "401" in op["responses"], (method, path)
        if "personBearer" in _schemes(api, op):
            assert op["responses"]["401"] == {"$ref": "#/components/responses/Unauthorized"} or path == "/instances", (method, path)


def test_the_admin_header_is_local_development_only():
    api = _api()
    scheme = api["components"]["securitySchemes"]["adminHeader"]
    assert scheme["name"] == names.DEMO_TOKEN_HEADER and "local development only" in scheme["description"].lower()
    assert names.LOCAL_DEV_ENV in scheme["description"] and "BURN_LOCAL_DEV" in api["info"]["description"]


def test_created_by_and_actor_are_nullable_and_leave_owner_alone():
    api = _api()["components"]["schemas"]
    assert api["CreatedBy"]["nullable"] is True and api["CreatedBy"]["type"] == "string"
    for schema in ("SessionStarted", "Instance", "EnrollToken"):
        assert api[schema]["properties"]["created_by"] == {"$ref": "#/components/schemas/CreatedBy"}
    assert api["Instance"]["properties"]["owner"] == {"type": "string", "nullable": True}  # still the team
    actor = json.loads((ROOT / "ledger-event.schema.json").read_text())["properties"]["actor"]
    assert actor["type"] == ["string", "null"]


def test_actor_is_a_person_an_instance_or_the_system():
    from burn_contracts import events
    schema = json.loads((ROOT / "ledger-event.schema.json").read_text())
    for actor in ("ada@example.com", "instance:sess_0123456789abcdef", "system", "admin", None):
        event = events.make_event(agent_id="agent-a", source="session", action="issued", actor=actor)
        jsonschema.validate(event, schema)
    for bad in ("", "someone", "instance:", "instance:a b", "a@b c", "x" * 400 + "@example.com"):
        with pytest.raises(ValueError):
            events.make_event(agent_id="agent-a", source="session", action="issued", actor=bad)
    event = events.make_event(agent_id="agent-a", source="session", action="issued")
    assert event["actor"] is None  # events without one stay valid
    jsonschema.validate(event, schema)


def test_sign_in_names():
    assert names.PEOPLE_POOL_NAME == "burn-people"
    assert [names.PEOPLE_USER_POOL_ID_PARAM, names.PEOPLE_CLIENT_ID_PARAM,
            names.PEOPLE_DOMAIN_PARAM, names.PEOPLE_ISSUER_PARAM] == [
        "/burn/people/user-pool-id", "/burn/people/client-id", "/burn/people/domain", "/burn/people/issuer"]
    assert names.LOCAL_DEV_ENV == "BURN_LOCAL_DEV"
    assert names.PEOPLE_AUTH_ENV == "BURN_PEOPLE_AUTH"
    assert names.PEOPLE_ENABLED_PARAM == "/burn/people/enabled"
    assert names.CLI_LOOPBACK_PORT == 8976
    assert names.CLI_REDIRECT_URI == "http://localhost:8976/callback"
    assert names.DASHBOARD_REDIRECT_URI == "http://localhost:5173/"
    assert names.AUTOMATION_CLIENT_NAME == "burn-automation"
    assert names.AUTOMATION_CLIENT_ID_PARAM == "/burn/people/automation-client-id"
    assert names.AUTOMATION_PERSON_SECRET == "burn-system/automation-person"


# --- replace on burn ---------------------------------------------------------------------------

def test_roles_can_ask_for_a_replacement_when_an_instance_is_burned():
    schema = json.loads((ROOT / "burn.schema.json").read_text())
    prop = schema["$defs"]["role"]["properties"]["replace_on_burn"]
    assert prop["type"] == "boolean" and prop["default"] is False  # off unless a role asks for it
    assert "not when the whole role is burned" in prop["description"]
    role = {"name": "worker", "tools": ["read_inbox"], "replace_on_burn": True}
    jsonschema.validate({"version": 1, "roles": [role]}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"version": 1, "roles": [role | {"replace_on_burn": "yes"}]}, schema)
    fields = _api()["components"]["schemas"]["RoleFields"]["properties"]
    assert fields["replace_on_burn"] == {key: prop[key] for key in ("type", "default", "description")}


def test_progress_is_item_ids_only_with_fixed_limits():
    assert names.ITEM_ID_RE.pattern == r"^[A-Za-z0-9_.:-]{1,64}$"
    for good in ("w01-item-01", "T-1001", "a", "x" * 64, "ns:item_1.2"):
        assert names.is_item_id(good)
    for bad in (None, 7, "", "x" * 65, "has space", "a/b", "w01\n", "{{burn:ref:x}}", "é"):
        assert not names.is_item_id(bad)
    assert names.MAX_PROGRESS_ITEMS == 500 and names.MAX_PROGRESS_EVENTS == 50
    assert names.PROGRESS_STATES == ("started", "done")
    assert (names.PROGRESS_URL_ENV, names.TASK_ENV, names.RESUME_ENV) == (
        "BURN_PROGRESS_URL", "BURN_TASK", "BURN_RESUME")


def test_progress_rides_on_the_heartbeat_and_a_broken_rule_is_not_a_409():
    api = _api()
    schemas = api["components"]["schemas"]
    assert schemas["ItemId"]["pattern"] == names.ITEM_ID_RE.pattern
    op = api["paths"]["/instances/{session_id}/heartbeat"]["post"]
    assert op["requestBody"]["required"] is False  # a bare heartbeat stays valid
    body = op["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/Heartbeat"}
    progress = schemas["Heartbeat"]["properties"]["progress"]
    assert progress["maxItems"] == names.MAX_PROGRESS_EVENTS
    event = schemas["ProgressEvent"]
    assert event["required"] == ["item", "state"] and event["additionalProperties"] is False
    assert event["properties"]["item"] == {"$ref": "#/components/schemas/ItemId"}
    assert tuple(event["properties"]["state"]["enum"]) == names.PROGRESS_STATES
    assert "400" in op["responses"] and "409" in op["responses"]
    assert str(names.MAX_PROGRESS_ITEMS) in op["responses"]["400"]["description"]
    assert "Burn's clock" in op["description"]


def test_replacement_names():
    assert names.MAX_REPLACEMENT_GENERATION == 3  # at most 3 replacements per task, then a person decides
    assert names.REPLACEMENT_STATUSES == ("pending", "linked", "needs_person")
    assert names.CUTOFF_SOURCES == ("evidence", "burn_time")


def _nullable_ref(prop, target):
    return prop["allOf"] == [{"$ref": f"#/components/schemas/{target}"}] and prop["nullable"] is True


def test_instances_show_their_replacement_links_and_progress_counts_only():
    schemas = _api()["components"]["schemas"]
    props = schemas["Instance"]["properties"]
    assert props["generation"]["type"] == "integer" and props["generation"]["minimum"] == 0
    for link in ("replaces", "replaced_by"):  # session ids, null when there is no link
        assert props[link]["type"] == "string" and props[link]["nullable"] is True
    assert _nullable_ref(props["replacement"], "Replacement")
    assert props["items_done"]["type"] == props["items_started"]["type"] == "integer"
    assert "progress" not in props  # list replies stay small: never the per-item map
    replacement = schemas["Replacement"]
    assert set(replacement["required"]) == {"status", "cutoff", "cutoff_source", "done", "quarantined"}
    rprops = replacement["properties"]
    assert tuple(rprops["status"]["enum"]) == names.REPLACEMENT_STATUSES
    assert tuple(rprops["cutoff_source"]["enum"]) == names.CUTOFF_SOURCES
    assert rprops["cutoff"]["format"] == "date-time"
    for key in ("done", "quarantined"):
        assert rprops[key]["items"] == {"$ref": "#/components/schemas/ItemId"}


def test_the_replace_route_needs_a_person_and_documents_every_refusal():
    api = _api()
    assert api["paths"]["/instances/{session_id}"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"] == {"$ref": "#/components/schemas/Instance"}
    op = api["paths"]["/instances/{session_id}/replace"]["post"]
    assert "security" not in op  # the global person (or, before sign-in, admin) requirement; never an instance
    assert _schemes(api, op) == {"personBearer", "adminHeader"}
    body = op["requestBody"]["content"]["application/json"]["schema"]
    assert op["requestBody"]["required"] is True
    assert body["required"] == ["session_id"] and body["additionalProperties"] is False
    assert {"200", "400", "401", "404", "409"} <= set(op["responses"])
    result = op["responses"]["200"]["content"]["application/json"]["schema"]
    assert result == {"$ref": "#/components/schemas/ReplaceResult"}
    required = set(api["components"]["schemas"]["ReplaceResult"]["required"])
    assert {"replaces", "done", "quarantined", "cutoff"} <= required
    assert "pending" in op["responses"]["409"]["description"]
    for rule in ("same role", "active", "after", "already"):
        assert rule in op["responses"]["400"]["description"], rule
    assert "TransactWriteItems" in op["description"] or "exactly one" in op["description"]


def test_replaced_and_resumed_events_link_the_two_instances():
    old, new = "sess_burned0123", "sess_fresh4567"
    replaced = events.make_event(
        agent_id=WORKER, source="instance", action="replaced", session_id=old, role="worker",
        replaced_by=new, actor="ada@example.com", detail="replaced by agent-worker-0b7c2e")
    resumed = events.make_event(
        agent_id="agent-worker-0b7c2e", source="instance", action="resumed", session_id=new, role="worker",
        replaces=old, actor="admin", detail="4 done, 1 quarantined")
    for event in (replaced, resumed):
        jsonschema.validate(event, EVENT_SCHEMA)
    assert {"replaced", "resumed"} <= events.ACTIONS
    description = EVENT_SCHEMA["properties"]["action"]["description"]
    assert "replaced =" in description and "resumed =" in description
    assert events.make_event(agent_id="agent-a", source="burn", action="revoked")["replaces"] is None
    with pytest.raises(ValueError):
        events.make_event(agent_id=WORKER, source="instance", action="resumed", replaces="has space")


def test_progress_events_are_checked_by_the_named_rule():
    from burn_contracts import progress
    assert progress.check_events([{"item": "w01-item-01", "state": "started"}]) == [("w01-item-01", "started")]
    assert progress.check_events([]) == []
    cases = [
        ({"item": "w01"}, "item and state"), ({"item": "w01", "state": "done", "note": "x"}, "item and state"),
        ({"item": "has space", "state": "done"}, "item id"), ({"item": "w01", "state": "finished"}, "state"),
        ("w01", "item and state"),
    ]
    for event, rule in cases:
        with pytest.raises(progress.ProgressError, match=rule):
            progress.check_events([event])
    with pytest.raises(progress.ProgressError, match="progress must be a list"):
        progress.check_events({"item": "w01", "state": "done"})
    with pytest.raises(progress.ProgressError, match=f"at most {names.MAX_PROGRESS_EVENTS} progress events"):
        progress.check_events([{"item": f"i{n}", "state": "started"} for n in range(names.MAX_PROGRESS_EVENTS + 1)])
    assert issubclass(progress.ProgressError, ValueError)


def test_progress_is_stamped_by_the_caller_clock_and_never_moves():
    from burn_contracts import progress
    t1, t2, t3 = "2026-11-07T10:00:00.000Z", "2026-11-07T10:00:05.000Z", "2026-11-07T10:00:09.000Z"
    state = progress.apply({}, [("a", "started"), ("b", "done")], t1)
    assert state == {"a": {"started_at": t1, "done_at": None}, "b": {"started_at": t1, "done_at": t1}}
    later = progress.apply(state, [("a", "started"), ("a", "done"), ("b", "done"), ("c", "started")], t2)
    assert later["a"] == {"started_at": t1, "done_at": t2}  # a second started keeps the first time
    assert later["b"] == {"started_at": t1, "done_at": t1}  # so does a second done
    assert progress.apply(later, [("a", "started")], t3)["a"] == {"started_at": t1, "done_at": t2}
    assert state["a"]["done_at"] is None  # the input is never changed
    assert progress.counts(later) == {"items_started": 3, "items_done": 2}
    assert progress.counts(None) == {"items_started": 0, "items_done": 0}
    full = progress.apply({}, [(f"i{n}", "done") for n in range(names.MAX_PROGRESS_ITEMS)], t1)
    assert progress.apply(full, [("i0", "done")], t2) == full  # known items never count again
    with pytest.raises(progress.ProgressError, match=f"at most {names.MAX_PROGRESS_ITEMS} items"):
        progress.apply(full, [("one-more", "started")], t2)


def test_only_work_done_before_the_cutoff_is_trusted():
    from burn_contracts import progress
    cutoff = "2026-11-07T10:00:05.000Z"
    before, at, after = "2026-11-07T10:00:01.000Z", cutoff, "2026-11-07T10:00:07.000Z"
    state = {
        "done-before": {"started_at": before, "done_at": before},
        "done-at-cutoff": {"started_at": before, "done_at": at},       # not before it: not trusted
        "in-flight": {"started_at": before, "done_at": None},          # working on it at the breach
        "done-after": {"started_at": before, "done_at": after},
        "started-after": {"started_at": after, "done_at": None},
        "started-and-done-after": {"started_at": after, "done_at": after},
    }
    done, quarantined = progress.classify(state, cutoff)
    assert done == ["done-before"]
    assert quarantined == sorted(set(state) - {"done-before"})
    assert progress.classify({}, cutoff) == ([], [])


def test_the_cutoff_compares_times_not_strings():
    from burn_contracts import progress
    # Other components may write "Z" or "+00:00" and any number of fraction digits. As strings, "...:00Z" sorts
    # after "...:00.500Z" ('Z' > '.'), which would trust an item done half a second after the breach.
    cutoff = "2026-11-07T10:00:00Z"
    state = {
        "half-second-after": {"started_at": "2026-11-07T09:59:59.000Z", "done_at": "2026-11-07T10:00:00.500Z"},
        "same-instant-other-format": {"started_at": "2026-11-07T09:59:00Z", "done_at": "2026-11-07T10:00:00.000+00:00"},
        "just-before": {"started_at": "2026-11-07T09:59:00Z", "done_at": "2026-11-07T09:59:59.999999+00:00"},
    }
    assert progress.classify(state, cutoff) == (["just-before"], ["half-second-after", "same-instant-other-format"])
    micro_cutoff = "2026-11-07T10:00:00.123456+00:00"
    assert progress.classify({"x": {"started_at": cutoff, "done_at": "2026-11-07T10:00:00.123Z"}}, micro_cutoff) == (
        ["x"], [])
    assert progress.parse_time("2026-11-07T12:00:00+02:00") == progress.parse_time("2026-11-07T10:00:00Z")
    for bad in ("2026-11-07T10:00:00", "yesterday", None, 5):  # no time zone, or not a timestamp at all
        with pytest.raises(ValueError):
            progress.parse_time(bad)


def test_a_burned_instance_learns_it_before_its_body_is_checked():
    description = _api()["paths"]["/instances/{session_id}/heartbeat"]["post"]["description"]
    assert "404, then 409, then 400" in description


def test_site_names():
    assert names.SITE_URL_PARAM == "/burn/site/url"
    assert names.API_URL_PARAM == "/burn/api/url"
    assert names.DASHBOARD_URL_PARAM == "/burn/dashboard/url"
    assert names.DASHBOARD_ORIGIN_ENV == "BURN_DASHBOARD_ORIGIN"
