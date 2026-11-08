import json
import secrets

import pytest
import yaml
from fastapi.testclient import TestClient

from burn_api_mock import create_app
from mock_data import CONTRACTS, load_scenario


@pytest.fixture
def scenario(tmp_path):
    data = load_scenario()
    # A different graph and ids prove that behavior comes from data, not demo special cases.
    text = json.dumps(data)
    for agent in data["agents"]:
        text = text.replace(agent["id"], "agent-" + secrets.token_hex(4))
    data = json.loads(text)
    data["agents"][0]["tools"].append("query_customers")
    data["customers"][0]["notes"] = data["files"][0]["content"]
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(data))
    return path, data


@pytest.fixture
def client(scenario):
    app = create_app(scenario[0], activity_interval=0)
    with TestClient(app, headers={"x-burn-demo-token": "mock"}) as client:  # the mock takes any admin value
        yield client


def response_schema(contract, path, method, status):
    spec = yaml.safe_load((CONTRACTS / contract).read_text())

    def expand(schema):
        if isinstance(schema, list):
            return [expand(item) for item in schema]
        if not isinstance(schema, dict):
            return schema
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref == "ledger-event.schema.json":
                return json.loads((CONTRACTS / ref).read_text())
            target = spec
            for part in ref.removeprefix("#/").split("/"):
                target = target[part]
            return expand(target)
        result = {key: expand(value) for key, value in schema.items() if key != "nullable"}
        if schema.get("nullable"):
            return {"anyOf": [result, {"type": "null"}]}
        return result

    return expand(spec["paths"][path][method]["responses"][str(status)]["content"]["application/json"]["schema"])
