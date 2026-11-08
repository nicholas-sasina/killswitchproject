"""BurnData: which tables expire rows, and that the events stream survives the TTL change."""
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from burn_infra.data import BurnDataStack  # noqa: E402
from burn_contracts import events, names  # noqa: E402  (path added by burn_infra)

ENV = cdk.Environment(account="123456789012", region="us-east-1")
# Transient rows only: the event ledger, delivered messages and spent enrollment tokens.
EXPIRING = {"burn-events", "burn-messages", names.ENROLL_TOKENS_TABLE}


@pytest.fixture(scope="module")
def tables() -> dict[str, dict]:
    template = Template.from_stack(BurnDataStack(cdk.App(), "BurnData", env=ENV))
    return {t["Properties"]["TableName"]: t["Properties"]
            for t in template.find_resources("AWS::DynamoDB::Table").values()}


def test_only_the_transient_tables_expire_rows(tables):
    expiring = {name for name, props in tables.items() if "TimeToLiveSpecification" in props}
    assert expiring == EXPIRING


def test_expiring_tables_read_the_contracts_ttl_attribute(tables):
    for name in EXPIRING:
        assert tables[name]["TimeToLiveSpecification"] == {
            "AttributeName": events.TTL_ATTRIBUTE, "Enabled": True}


def test_the_events_stream_still_carries_new_rows(tables):
    # notifications reads this stream; the TTL must not have replaced it.
    assert tables["burn-events"]["StreamSpecification"] == {"StreamViewType": "NEW_IMAGE"}
    assert all("StreamSpecification" not in props
               for name, props in tables.items() if name != "burn-events")
