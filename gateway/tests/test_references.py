"""Secret references: the agent gets {{burn:ref:...}} instead of any credential Burn recognizes."""
import base64
import json
import secrets
import string

import pytest

import detection
import exit_check
import interceptor
import redact
from burn_contracts import fingerprint as fp
from conftest import bump_version, events, ledger_rows, put_session, response_event, tool_call, tool_result

ALNUM = string.ascii_letters + string.digits


def _rand(n):
    return "".join(secrets.choice(ALNUM) for _ in range(n))


def respond(session, *texts, tool="read_config", arguments=None, trace="Root=1-a", rpc_id=1):
    body = tool_call(tool, arguments if arguments is not None else {"path": "config/app.env"}, rpc_id)
    reply = tool_result(texts[0], rpc_id)
    reply["result"]["content"] += [{"type": "text", "text": t} for t in texts[1:]]
    out = interceptor.handler(response_event(body, session["client_id"], reply, trace), None)
    return out["mcp"]["transformedGatewayResponse"]["body"]


def delivered(body) -> str:
    return "\n".join(c["text"] for c in body["result"]["content"])


def register_api_key(aws, agent="agent-alpha", canary=False, ref=None):
    """An mpk_live_ key registered like the minter (whole value exact, body shingles)."""
    body = _rand(32)
    value = "mpk_" + "live_" + body
    key = detection.scanner().key
    ref = ref or f"burn/{agent}/api_key"
    base = {"secret_ref": ref, "owner_agent_id": agent, "secret_type": "other" if canary else "api_key",
            "version": 1, "status": "active", **({"canary": True, "session_id": "s-x"} if canary else {})}
    rows = {fp.fingerprint(value, key): {**base, "part": "value", "kind": "exact"}}
    windows = fp.shingle_fingerprints(body, key)
    for index, digest in windows:
        rows.setdefault(digest, {**base, "part": "random", "kind": "shingle", "index": index, "n_shingles": len(windows)})
    with aws.Table("burn-fingerprints").batch_writer() as batch:
        for digest, row in rows.items():
            batch.put_item(Item={"fingerprint": digest, **row})
    bump_version(aws)
    return value


def register_connection_string(aws):
    """A shared connection string registered like the minter: whole value + random part (exact and shingles)."""
    random_part = _rand(24)
    value = f"postgres://svc_crm:{random_part}@crm-db.internal.example:5432/crm"
    key = detection.scanner().key
    base = {"secret_ref": "burn/shared/crm_db_url", "owner_agent_id": "shared", "secret_type": "other",
            "version": 1, "status": "active"}
    rows = {fp.fingerprint(value, key): {**base, "part": "value", "kind": "exact"},
            fp.fingerprint(random_part, key): {**base, "part": "random", "kind": "exact"}}
    windows = fp.shingle_fingerprints(random_part, key)
    for index, digest in windows:
        rows.setdefault(digest, {**base, "part": "random", "kind": "shingle", "index": index, "n_shingles": len(windows)})
    with aws.Table("burn-fingerprints").batch_writer() as batch:
        for digest, row in rows.items():
            batch.put_item(Item={"fingerprint": digest, **row})
    bump_version(aws)
    return value, random_part


def leaked(text):
    """Recognized, non-canary credentials still in what the agent got (the references probe)."""
    return redact.targets(detection.scan(text))


# ---- replace ---------------------------------------------------------------------------------

def test_api_key_becomes_a_reference(aws):
    session = put_session(aws)
    value = register_api_key(aws)
    body = respond(session, f"PROVIDER_API_KEY={value}\nREGION=us-east-1")
    assert delivered(body) == "PROVIDER_API_KEY={{burn:ref:burn/agent-alpha/api_key}}\nREGION=us-east-1"
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["status"] == "referenced"


def test_connection_string_keeps_host_and_database(aws):
    session = put_session(aws)
    value, random_part = register_connection_string(aws)
    body = respond(session, f"DATABASE_URL={value}")
    assert delivered(body) == ("DATABASE_URL=postgres://svc_crm:{{burn:ref:burn/shared/crm_db_url}}"
                               "@crm-db.internal.example:5432/crm")
    assert random_part not in delivered(body)


@pytest.mark.parametrize("encode", [
    lambda v: v,
    lambda v: base64.b64encode(v.encode()).decode(),
    lambda v: v.encode().hex(),
    lambda v: base64.b64encode(v.encode().hex().encode()).decode(),
    lambda v: f"https://hooks.example/notify?token={v}",
    lambda v: json.dumps({"auth": v}),
    lambda v: v[::-1],
    lambda v: " ".join(v),
    lambda v: v[:36],  # 27 of 32 random chars: recognized as a fragment, cannot be located
], ids=["plain", "base64", "hex", "base64-hex", "url-param", "json", "reversed", "separators", "fragment"])
def test_no_recognized_credential_reaches_the_agent_in_any_transport(aws, encode):
    session = put_session(aws)
    value = register_api_key(aws)
    body = respond(session, f"note: {encode(value)} end")
    text = delivered(body)
    if body["result"].get("isError"):  # withheld: counted separately by the probe, and fine
        assert text.startswith("BURN: tool output withheld")
        assert ledger_rows(aws, "agent-alpha") == {}
        [refused] = events(aws)
        assert refused["action"] == "refused" and refused["secret_ref"] == "burn/agent-alpha/api_key"
    else:
        assert leaked(text) == []  # the references probe: nothing recognized reaches the agent
    assert value not in json.dumps(events(aws), default=str)


def test_a_copy_left_after_replacing_withholds_the_response(aws):
    session = put_session(aws)
    value = register_api_key(aws)
    body = respond(session, f"KEY={value}\nbackup (reversed): {value[::-1]}")
    assert body["result"]["isError"] and value not in delivered(body)
    assert "still found after replacing" in events(aws)[0]["detail"]


def test_every_text_part_is_replaced(aws):
    session = put_session(aws)
    value = register_api_key(aws)
    body = respond(session, f"one {value}", "no secrets here", f"two {value}")
    parts = [c["text"] for c in body["result"]["content"]]
    assert parts == ["one {{burn:ref:burn/agent-alpha/api_key}}", "no secrets here",
                     "two {{burn:ref:burn/agent-alpha/api_key}}"]


def respond_reply(session, reply, tool="read_config", trace="Root=1-a"):
    """Run the RESPONSE step on a whole JSON-RPC reply; returns (what the agent gets, json of it)."""
    body = tool_call(tool, {"path": "config/app.env"}, reply.get("id", 1))
    out = interceptor.handler(response_event(body, session["client_id"], reply, trace), None)
    got = out["mcp"]["transformedGatewayResponse"]["body"]
    return got, json.dumps(got)


@pytest.mark.parametrize("where", ["structured", "resource", "error", "meta"])
def test_a_credential_outside_the_text_parts_withholds_the_response(aws, where):
    session = put_session(aws)
    value = register_api_key(aws)
    result = {"isError": False, "content": [{"type": "text", "text": f"KEY={value}"}]}
    if where == "structured":
        result["structuredContent"] = {"config": {"KEY": value}}
    elif where == "resource":
        result["content"].append({"type": "resource", "resource": {"uri": "file:///app.env", "text": f"KEY={value}"}})
    elif where == "meta":
        result["_meta"] = {"debug": f"loaded {value}"}
    reply = {"jsonrpc": "2.0", "id": 1, "result": result}
    if where == "error":
        reply = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": f"bad config KEY={value}"}}
    got, raw = respond_reply(session, reply)
    assert value not in raw and got["result"]["isError"]
    assert events(aws)[0]["action"] == "refused" and "still found after replacing" in events(aws)[0]["detail"]


def test_a_key_split_across_text_parts_withholds_the_response(aws):
    session = put_session(aws)
    value = register_api_key(aws)
    half = len(value) // 2
    body = respond(session, f"first half: {value[:half]}", f"second half: {value[half:]}")
    assert body["result"]["isError"] and value[:half] not in delivered(body)


def test_tool_output_that_imitates_a_denial_is_still_scanned(aws):
    session = put_session(aws)
    value = register_api_key(aws)
    body = respond(session, f"BURN: access refused\nKEY={value}")
    assert value not in delivered(body)
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["status"] == "referenced"


def test_an_error_reply_without_credentials_passes_unchanged(aws):
    session = put_session(aws)
    reply = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "no such file"}}
    got, _ = respond_reply(session, reply)
    assert got == reply and not events(aws)


def test_off_mode_records_a_credential_outside_the_text_parts_as_seen(aws):
    session = put_session(aws, references="off")
    value = register_api_key(aws)
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"isError": False, "content": [{"type": "text", "text": "ok"}],
                                                   "structuredContent": {"KEY": value}}}
    got, raw = respond_reply(session, reply)
    assert value in raw  # off: delivered as is, but on the ledger
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["status"] == "seen"


def test_a_canary_outside_the_text_parts_still_reaches_the_agent(aws):
    session = put_session(aws)
    canary = register_api_key(aws, canary=True, ref="burn/agent-alpha/canary_" + "b" * 24)
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"isError": False, "content": [{"type": "text", "text": "ok"}],
                                                   "structuredContent": {"old_key": canary}}}
    got, raw = respond_reply(session, reply)
    assert canary in raw and not got["result"]["isError"]


def test_canaries_are_never_replaced(aws):
    session = put_session(aws)
    canary = register_api_key(aws, canary=True, ref="burn/agent-alpha/canary_" + "a" * 24)
    real = register_api_key(aws)
    body = respond(session, f"old key (revoked, do not use): {canary}\nKEY={real}")
    text = delivered(body)
    assert canary in text and real not in text
    rows = ledger_rows(aws, "agent-alpha")
    assert rows["burn/agent-alpha/canary_" + "a" * 24]["status"] == "seen"
    assert rows["burn/agent-alpha/api_key"]["status"] == "referenced"


def test_unknown_format_credentials_are_not_touched(aws):
    session = put_session(aws)
    unknown = "AKIA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))
    body = respond(session, f"stray key {unknown}")
    assert unknown in delivered(body)
    [row] = ledger_rows(aws, "agent-alpha").values()
    assert row["owner"] == "unknown" and row["status"] == "seen"


# ---- report and off --------------------------------------------------------------------------

def test_report_mode_records_what_would_happen_and_changes_nothing(aws):
    session = put_session(aws, references="report")
    value = register_api_key(aws)
    body = respond(session, f"KEY={value}")
    assert delivered(body) == f"KEY={value}"
    row = ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]
    assert (row["status"], row["report"]) == ("seen", "would replace with a reference")
    assert events(aws)[0]["detail"].endswith("report mode: would replace with a reference")


def test_report_mode_says_when_it_would_withhold(aws):
    session = put_session(aws, references="report")
    value = register_api_key(aws)
    body = respond(session, f"reversed: {value[::-1]}")
    assert value[::-1] in delivered(body)
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["report"].startswith("would withhold")


def test_seen_wins_once_plaintext_reached_the_agent(aws):
    value = register_api_key(aws)
    replace = put_session(aws)
    respond(replace, f"KEY={value}", trace="Root=1-a")
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["status"] == "referenced"
    aws.Table("burn-sessions").update_item(Key={"session_id": replace["session_id"]},
                                           UpdateExpression="SET #r = :r", ExpressionAttributeNames={"#r": "references"},
                                           ExpressionAttributeValues={":r": "off"})
    respond(replace, f"KEY={value}", trace="Root=1-b", rpc_id=2)
    aws.Table("burn-sessions").update_item(Key={"session_id": replace["session_id"]},
                                           UpdateExpression="REMOVE #r", ExpressionAttributeNames={"#r": "references"})
    respond(replace, f"KEY={value}", trace="Root=1-c", rpc_id=3)
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["status"] == "seen"


def test_a_row_without_a_status_is_never_turned_into_referenced(aws):
    """Rows from before statuses existed recorded plaintext; a later replaced read must not hide them from a burn."""
    value = register_api_key(aws)
    aws.Table("burn-exposures").put_item(Item={
        "agent_id": "agent-alpha", "secret_ref_or_fp": "burn/agent-alpha/api_key", "secret_ref": "burn/agent-alpha/api_key",
        "fingerprint": "f" * 64, "source": "tool_response", "via": "read_config", "first_seen": "2026-11-07T00:00:00.000Z",
        "last_seen": "2026-11-07T00:00:00.000Z", "seen_in": {"Root=1-old#1"}})
    session = put_session(aws)
    body = respond(session, f"KEY={value}")
    assert value not in delivered(body)  # the agent still only gets the reference
    row = ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]
    assert row["status"] == "seen" and "Root=1-a#1" in row["seen_in"]
    respond(session, f"KEY={value}")  # a retry of the same call changes nothing
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["seen_in"] == {"Root=1-old#1", "Root=1-a#1"}


# ---- source_ref and handoff ------------------------------------------------------------------

def test_source_ref_comes_from_the_tools_resource_argument(aws):
    session = put_session(aws, tools=("read_ticket", "list_tickets"))
    value = register_api_key(aws)
    respond(session, f"ticket body {value}", tool="read_ticket", arguments={"ticket_id": "T-1001"})
    assert ledger_rows(aws, "agent-alpha")["burn/agent-alpha/api_key"]["source_ref"] == "read_ticket:T-1001"
    other = register_api_key(aws, agent="agent-beta")
    respond(session, f"list {other}", tool="list_tickets", arguments={}, trace="Root=1-b", rpc_id=2)
    assert ledger_rows(aws, "agent-alpha")["burn/agent-beta/api_key"]["source_ref"] == "list_tickets"


def test_handoff_carries_status_and_source_ref(aws):
    sender = put_session(aws)
    value = register_api_key(aws)
    respond(sender, f"KEY={value}")
    exit_check.inherit(sender, "agent-beta", "Root=1-h#1")
    row = ledger_rows(aws, "agent-beta")["burn/agent-alpha/api_key"]
    assert (row["source"], row["via"], row["status"], row["source_ref"]) == (
        "handoff", "agent-alpha", "referenced", "read_config:config/app.env")


def test_no_source_ref_attribute_is_written_when_there_is_none(aws):
    entry = {"secret_ref_or_fp": "burn/shared/x", "secret_ref": "burn/shared/x", "fingerprint": "f" * 64,
             "secret_type": "other", "owner": "shared", "owner_agent_id": None}
    from ledger import copy_entry
    copy_entry(entry, "agent-beta", via="agent-alpha", call="Root=1-z#1")
    assert "source_ref" not in ledger_rows(aws, "agent-beta")["burn/shared/x"]


def test_fragments_below_the_threshold_are_not_recognized_and_pass(aws):
    session = put_session(aws)
    value = register_api_key(aws)
    body = respond(session, f"note: {value[:30]} end")  # 21 of 32 random chars
    assert delivered(body) == f"note: {value[:30]} end" and ledger_rows(aws, "agent-alpha") == {}


# ---- resolving references in tool arguments -------------------------------------------------

def call_with(session, tool, arguments):
    from conftest import request_event
    out = interceptor.handler(request_event(tool_call(tool, arguments), session["client_id"]), None)
    mcp = out["mcp"]
    if "transformedGatewayRequest" in mcp:
        return "passed", mcp["transformedGatewayRequest"]["body"]["params"]["arguments"]
    return "refused", delivered(mcp["transformedGatewayResponse"]["body"])


@pytest.fixture
def accepts_refs(monkeypatch):
    from burn_contracts import tools
    monkeypatch.setattr(tools, "ACCEPTS_SECRET_REFS", {"query_customers": ("query",), "send_message": ("text",)})


def store_secret(name, value):
    import boto3
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(Name=name, SecretString=value)


def test_own_credential_reference_is_resolved_for_the_tool(aws, accepts_refs):
    session = put_session(aws, tools=("query_customers",))
    store_secret("burn/agent-alpha/api_key", "mpk_" + "live_" + _rand(32))
    status, args = call_with(session, "query_customers", {"query": "key {{burn:ref:burn/agent-alpha/api_key}}"})
    assert status == "passed" and args["query"].startswith("key mpk_live_")


def test_a_reference_from_the_ledger_is_resolved(aws, accepts_refs):
    session = put_session(aws, tools=("query_customers", "read_config"))
    shared, _ = register_connection_string(aws)
    store_secret("burn/shared/crm_db_url", shared)
    respond(session, f"DB={shared}")  # the agent was given the reference here
    status, args = call_with(session, "query_customers", {"query": "{{burn:ref:burn/shared/crm_db_url}}"})
    assert (status, args["query"]) == ("passed", shared)


@pytest.mark.parametrize("ref", [
    "burn/agent-beta/api_key",                    # someone else's, never in this ledger
    "burn/agent-alpha/canary_" + "a" * 24,        # a canary
    "burn/agent-alpha/badge",                     # the badge secret
    "burn/shared/never_seen",                     # shared, but never given to this agent
])
def test_other_references_are_refused(aws, accepts_refs, ref):
    session = put_session(aws, tools=("query_customers",))
    status, text = call_with(session, "query_customers", {"query": "{{burn:ref:" + ref + "}}"})
    assert status == "refused" and "reference not allowed" in text


def test_a_reference_received_in_a_message_is_not_resolved(aws, accepts_refs):
    sender = put_session(aws, tools=("query_customers", "read_config"))
    shared, _ = register_connection_string(aws)
    store_secret("burn/shared/crm_db_url", shared)
    respond(sender, f"DB={shared}")
    exit_check.inherit(sender, "agent-beta", "Root=1-h#1")  # agent-beta got a message from agent-alpha
    receiver = put_session(aws, agent_id="agent-beta", tools=("query_customers",))
    status, text = call_with(receiver, "query_customers", {"query": "{{burn:ref:burn/shared/crm_db_url}}"})
    assert status == "refused" and "reference not allowed" in text


def test_a_reference_to_a_rotated_version_is_not_resolved(aws, accepts_refs):
    session = put_session(aws, tools=("query_customers", "read_config"))
    shared, _ = register_connection_string(aws)
    store_secret("burn/shared/crm_db_url", shared)
    respond(session, f"DB={shared}")
    table = aws.Table("burn-fingerprints")
    for row in table.scan()["Items"]:  # what the minter does on rotation: the old version's rows are kept, as rotated
        if row.get("secret_ref") == "burn/shared/crm_db_url":
            table.put_item(Item={**row, "status": "rotated"})
    bump_version(aws)
    status, text = call_with(session, "query_customers", {"query": "{{burn:ref:burn/shared/crm_db_url}}"})
    assert status == "refused" and "reference not allowed" in text


def test_a_credential_that_cannot_be_read_refuses_the_call(aws, accepts_refs):
    session = put_session(aws, tools=("query_customers",))  # own key, but no secret stored
    status, text = call_with(session, "query_customers", {"query": "{{burn:ref:burn/agent-alpha/api_key}}"})
    assert status == "refused" and "credential unavailable" in text


def test_a_failing_check_refuses_the_call(aws, accepts_refs, monkeypatch):
    import resolve
    session = put_session(aws, tools=("query_customers",))

    def denied(*args, **kwargs):
        raise RuntimeError("AccessDenied")
    monkeypatch.setattr(resolve._exposures(), "query", denied)
    status, text = call_with(session, "query_customers", {"query": "{{burn:ref:burn/shared/crm_db_url}}"})
    assert status == "refused" and "could not be checked" in text


def test_send_message_never_resolves_even_if_declared(aws, accepts_refs):
    session = put_session(aws)
    store_secret("burn/agent-alpha/api_key", "mpk_" + "live_" + _rand(32))
    status, args = call_with(session, "send_message",
                             {"to": "agent-beta", "text": "{{burn:ref:burn/agent-alpha/api_key}}"})
    assert status == "passed" and args["text"] == "{{burn:ref:burn/agent-alpha/api_key}}"


def test_undeclared_arguments_are_left_alone(aws):
    session = put_session(aws, tools=("query_customers",))
    status, args = call_with(session, "query_customers", {"query": "{{burn:ref:burn/agent-alpha/api_key}}"})
    assert (status, args["query"]) == ("passed", "{{burn:ref:burn/agent-alpha/api_key}}")


@pytest.mark.parametrize("template,expected", [
    ("postgres://svc:{pw}@db.example:5432/crm", "postgres://svc:{R}@db.example:5432/crm"),
    ("redis://:{pw}@cache.example/0", "redis://:{R}@cache.example/0"),
    ("https://api.example/v1/{pw}/hook", "{R}"),          # no user-info: replace all of it
    ("postgres://svc@db.example/{pw}", "{R}"),            # user, no password
])
def test_password_only_replacement(template, expected):
    url = template.replace("{pw}", _rand(16))  # generated at run time, like every test credential
    out = redact._replacement(url, "burn/shared/crm_db_url", "other")
    assert out == expected.replace("{R}", "{{burn:ref:burn/shared/crm_db_url}}")
