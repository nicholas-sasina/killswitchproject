import secrets

import jsonschema
from burn_contracts.names import agent_secret_ref, shared_secret_ref

from conftest import response_schema
from minter_mock import MinterMock, handle


def invoke(minter, method, path, body=None):
    response = minter.handle({"method": method, "path": path, "body": body or {}})
    assert response["statusCode"] == 200
    return response["body"]


def test_issue_idempotency_rotate_flag_delete(scenario):
    minter = MinterMock()
    first, second = [agent["id"] for agent in scenario[1]["agents"]]
    path = f"/agents/{first}/credentials"
    before = invoke(minter, "POST", path, {"types": ["api_key", "aws_key"]})
    jsonschema.validate(before, response_schema("minter.openapi.yaml", "/agents/{agent_id}/credentials", "post", 200))
    assert invoke(minter, "POST", path, {"types": ["api_key", "aws_key"]}) == before
    invoke(minter, "POST", f"/agents/{second}/credentials", {"types": ["api_key"]})
    shared = invoke(minter, "POST", "/shared/credentials", scenario[1]["shared_credentials"][0])
    assert invoke(minter, "POST", "/shared/credentials", scenario[1]["shared_credentials"][0]) == shared
    refs = [row["secret_ref"] for row in before["credentials"]]
    other_ref = agent_secret_ref(second, "api_key")
    fingerprints = {ref: minter.credentials.rows[ref]["fingerprint"] for ref in refs}
    other_fingerprint = minter.credentials.rows[other_ref]["fingerprint"]
    result = invoke(minter, "POST", f"/agents/{first}/rotate", {"exposed_refs": refs + [shared["secret_ref"], other_ref]})
    assert set(result["rotated"]) == set(refs)
    assert set(result["flagged"]) == {shared["secret_ref"], other_ref}
    after = invoke(minter, "POST", path, {"types": ["api_key", "aws_key"]})
    assert all(row["version"] == 2 for row in after["credentials"])
    assert all(minter.credentials.rows[ref]["fingerprint"] != fingerprints[ref] for ref in refs)
    assert minter.credentials.rows[other_ref]["fingerprint"] == other_fingerprint
    activity = invoke(minter, "GET", f"/agents/{first}/key-activity")
    jsonschema.validate(activity, response_schema("minter.openapi.yaml", "/agents/{agent_id}/key-activity", "get", 200))
    assert len(activity["events"]) == 3
    assert all(len(event["key_hint"]) == 4 for event in activity["events"])
    assert [key["status"] for key in activity["keys"]] == ["inactive", "active"]
    future = minter.handle({"method": "GET", "path": f"/agents/{first}/key-activity",
                            "query": {"since": "9999-01-01T00:00:00Z"}})
    assert future["statusCode"] == 200
    assert future["body"]["events"] == []
    assert set(invoke(minter, "DELETE", path)["deleted"]) == set(refs)
    assert invoke(minter, "DELETE", path)["deleted"] == []
    assert invoke(minter, "GET", f"/agents/{first}/key-activity")["events"] == []
    assert invoke(minter, "DELETE", "/shared/credentials")["deleted"] == [shared["secret_ref"]]
    assert other_ref in minter.credentials.rows


def test_invalid_requests_and_direct_invoke():
    minter = MinterMock()
    agent_id = "agent-" + secrets.token_hex(4)
    assert handle({"method": "GET", "path": f"/agents/{agent_id}/key-activity"})["statusCode"] == 200
    requests = [
        {"method": "POST", "path": f"/agents/{agent_id}/credentials", "body": {"types": ["other"]}},
        {"method": "POST", "path": "/shared/credentials", "body": {"name": "invalid/name"}},
        {"method": "POST", "path": f"/agents/{agent_id}/rotate", "body": {"exposed_refs": "wrong"}},
        {"method": "POST", "path": f"/agents/{agent_id}/credentials", "body": "wrong"},
        {"method": "GET", "path": f"/agents/{agent_id}/key-activity", "query": {"since": "wrong"}},
    ]
    assert all(minter.handle(request)["statusCode"] == 400 for request in requests)
    assert minter.handle({"method": "GET", "path": "/missing"})["statusCode"] == 404
    assert minter.handle({"method": "GET", "path": f"/agents/{agent_id}/credentials"})["statusCode"] == 405


def test_fake_values_are_fresh_and_rendered_only_in_memory(scenario):
    from mock_data import Credentials

    first, second = Credentials(), Credentials()
    first.load(scenario[1])
    second.load(scenario[1])
    different = all(first.rows[ref]["fingerprint"] != second.rows[ref]["fingerprint"] for ref in first.rows)
    assert different
    content = first.render(scenario[1]["files"][0]["content"])
    rendered = "{{cred:" not in content
    assert rendered
    ref = shared_secret_ref(scenario[1]["shared_credentials"][0]["name"])
    template_replaced = "{random}" not in first.values[ref]
    assert template_replaced


def test_mocks_import_from_repository_root():
    import subprocess
    import sys
    from mock_data import CONTRACTS

    subprocess.run([sys.executable, "-c",
                    "from mocks.minter_mock import handle; "
                    "from mocks.burn_api_mock import create_app; "
                    "from mocks.mcp_tools_mock import ScenarioMCP; "
                    "assert handle({'method': 'DELETE', 'path': '/shared/credentials'})['statusCode'] == 200"],
                   cwd=CONTRACTS.parent, check=True, capture_output=True)
