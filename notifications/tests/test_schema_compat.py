"""Requirement 22: the parser stays compatible with Burn's real event contract."""
import json
from pathlib import Path

import jsonschema
import pytest
from burn_contracts import events as contract_events
from burn_contracts.events import make_event

from burn_notify import events, fixtures
from burn_notify.events import InvalidEvent, parse
from burn_notify.policy import classify

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "ledger-event.schema.json").read_text())


def test_enum_sets_match_contract():
    assert events.SOURCES == contract_events.SOURCES
    assert events.ACTIONS == contract_events.ACTIONS
    assert events.FLAG_LEVELS == contract_events.FLAG_LEVELS
    assert events.OWNERS == contract_events.OWNERS
    assert set(events.OPTIONAL) == set(contract_events.OPTIONAL_FIELDS)
    assert set(events.REQUIRED) == set(SCHEMA["required"])


@pytest.mark.parametrize("name", sorted(fixtures.ALL))
def test_fixtures_are_schema_valid(name):
    jsonschema.validate(fixtures.ALL[name](), SCHEMA)


def test_every_contract_action_classifies():
    for action in contract_events.ACTIONS:
        source = "provider" if action == "canary_used" else "burn"
        event = make_event(agent_id="agent-worker-abc123", source=source, action=action)
        assert classify(parse(event)).category


def test_make_event_output_parses():
    event = make_event(agent_id="agent-worker-abc123", source="burn", action="flagged",
                       secret_ref="burn/shared/db_password", owner="shared", flag_level="attempted")
    parsed = parse(event)
    assert parsed["flag_level"] == "attempted" and parsed["event_id"] == event["event_id"]


def test_stream_record_insert_parses_and_drops_partition_key():
    event = fixtures.canary_used()
    parsed = parse(fixtures.stream_record(event))
    assert parsed == event and "stream" not in parsed


def test_sqs_wrapped_stream_record_parses():
    event = fixtures.blocked()
    sqs = {"messageId": "m1", "receiptHandle": "r", "body": json.dumps(fixtures.stream_record(event))}
    assert parse(sqs) == event


@pytest.mark.parametrize("name", ["MODIFY", "REMOVE"])
def test_non_insert_records_are_ignored(name):
    assert parse(fixtures.stream_record(fixtures.blocked(), event_name=name)) is None


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(action="exfiltrated"),
    lambda e: e.update(source="internet"),
    lambda e: e.update(event_id="evt_bad"),
    lambda e: e.update(agent_id="<!here>"),
    lambda e: e.update(flag_level="critical"),
    lambda e: e.pop("ts"),
])
def test_invalid_events_are_rejected(mutate):
    event = fixtures.blocked()
    mutate(event)
    with pytest.raises(InvalidEvent):
        parse(event)


def test_free_text_in_identifier_fields_is_dropped():
    event = fixtures.canary_used()
    event["session_id"] = "sess <@U123> click https://evil.example"
    event["role"] = "Worker <script>"
    parsed = parse(event)
    assert parsed["session_id"] is None and parsed["role"] is None


def test_new_image_required():
    record = fixtures.stream_record(fixtures.blocked())
    del record["dynamodb"]["NewImage"]
    with pytest.raises(InvalidEvent):
        parse(record)
