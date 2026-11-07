"""Write a scenario into the burn-scenario table and mint its shared credentials.

    uv run python scripts/load_scenario.py scenarios/support-desk.yaml

Items follow the layout in contracts/README.md: pk = meta | agent#<id> |
ticket#<id> | file#<path> | customer#<id>, with the YAML object under `data`.
"""
import json
import sys
from pathlib import Path

import boto3
import yaml
from jsonschema import validate

from common import REPO_ROOT, REGION, invoke_minter, names


def scenario_items(scenario: dict) -> list[dict]:
    items = [{"pk": "meta", "data": {
        "name": scenario["name"],
        "description": scenario.get("description", ""),
        "agent_ids": [a["id"] for a in scenario["agents"]],
    }}]
    for key, id_field, prefix in (("agents", "id", "agent"), ("tickets", "id", "ticket"),
                                  ("files", "path", "file"), ("customers", "id", "customer")):
        items += [{"pk": f"{prefix}#{obj[id_field]}", "data": obj} for obj in scenario.get(key, [])]
    return items


def load(path: Path) -> dict:
    scenario = yaml.safe_load(path.read_text())
    schema = json.loads((REPO_ROOT / "contracts" / "scenario.schema.json").read_text())
    validate(scenario, schema)
    table = boto3.resource("dynamodb", region_name=REGION).Table(names.SCENARIO_TABLE)
    with table.batch_writer() as batch:
        for item in scenario_items(scenario):
            batch.put_item(Item=item)
    for cred in scenario.get("shared_credentials", []):
        invoke_minter("POST", "/shared/credentials", cred)
        print(f"shared credential {names.shared_secret_ref(cred['name'])} issued")
    print(f"scenario {scenario['name']} loaded ({len(scenario['agents'])} agents, {len(scenario['tickets'])} tickets)")
    return scenario


if __name__ == "__main__":
    load(Path(sys.argv[1]))
