"""Scenario loading and fake values, shared only by the development mocks."""

import json
import re
import secrets
import string
from pathlib import Path

import jsonschema
import yaml
from burn_contracts.fingerprint import fingerprint
from burn_contracts.names import agent_secret_ref, shared_secret_ref

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
DEFAULT_SCENARIO = CONTRACTS / "examples/minimal-scenario.yaml"
PLACEHOLDER = re.compile(r"\{\{cred:([^:}]+):([^}]+)\}\}")
CANARY = re.compile(r"\{\{canary\}\}")
# The text the real Gateway gives a placeholder it cannot fill (gateway/lambdas/tools/placeholders.py).
UNAVAILABLE = "(credential not issued)"


def load_scenario(path=DEFAULT_SCENARIO) -> dict:
    scenario = yaml.safe_load(Path(path).read_text())
    jsonschema.validate(scenario, json.loads((CONTRACTS / "scenario.schema.json").read_text()))
    ids = [agent["id"] for agent in scenario["agents"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Scenario agent ids must be unique")
    if any(set(agent.get("send_to", [])) - set(ids) for agent in scenario["agents"]):
        raise ValueError("Scenario recipients must refer to known agents")
    return scenario


def random_text(length: int, alphabet=string.ascii_letters + string.digits) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Credentials:
    def __init__(self):
        self.rows = {}
        self.values = {}
        self.fingerprint_key = secrets.token_bytes(32)

    def issue(self, agent_id: str, types: list[str]) -> list[dict]:
        return [self._put(agent_secret_ref(agent_id, kind), kind, agent_id) for kind in types]

    def shared(self, name: str, kind: str, template: str) -> dict:
        return self._put(shared_secret_ref(name), kind, "shared", template=template)

    def _put(self, ref, kind, owner, *, template=None, rotate=False):
        previous = self.rows.get(ref)
        if previous and not rotate:
            return self.public(ref)
        if template is not None:
            value = template.replace("{random}", random_text(24))
        elif kind == "api_key":
            value = "mpk_live_" + random_text(32)
        else:
            # These have IAM-compatible shapes but are never registered with AWS.
            value = {
                "access_key_id": "AKIA" + random_text(16, string.ascii_uppercase + string.digits),
                "secret_access_key": random_text(40, string.ascii_letters + string.digits + "+/"),
                "iam_user": f"burn-agent-{owner}",
            }
        self.values[ref] = value
        material = value["access_key_id"] if isinstance(value, dict) else value
        self.rows[ref] = {
            "secret_ref": ref, "type": kind, "owner": "shared" if owner == "shared" else "agent",
            "owner_agent_id": None if owner == "shared" else owner,
            "version": previous["version"] + 1 if previous else 1,
            "fingerprint": fingerprint(material, self.fingerprint_key),
        }
        return self.public(ref)

    def public(self, ref: str) -> dict:
        row = self.rows[ref]
        return {key: row[key] for key in ("secret_ref", "type", "owner", "version")}

    def rotate(self, agent_id: str, exposed_refs: list[str]) -> dict:
        owned = [ref for ref, row in self.rows.items() if row["owner_agent_id"] == agent_id]
        for ref in owned:
            row = self.rows[ref]
            self._put(ref, row["type"], agent_id, rotate=True)
        return {"rotated": sorted(owned), "flagged": sorted(set(exposed_refs) - set(owned))}

    def delete(self, owner: str) -> list[str]:
        refs = [ref for ref, row in self.rows.items()
                if (row["owner_agent_id"] or "shared") == owner]
        for ref in refs:
            del self.rows[ref]
            del self.values[ref]
        return sorted(refs)

    def render(self, text: str) -> str:
        """Fill placeholders like the Gateway does. The mocks issue no canaries, so {{canary}} gets the text a
        session without one gets there; left as the literal, it would point the agent at the trap."""
        text = CANARY.sub(UNAVAILABLE, text)

        def replace(match):
            owner, name = match.groups()
            kind, _, part = name.partition(".")
            ref = shared_secret_ref(kind) if owner == "shared" else agent_secret_ref(owner, kind)
            value = self.values[ref]
            return value[part] if part else str(value)

        return PLACEHOLDER.sub(replace, text)

    def load(self, scenario: dict):
        for agent in scenario["agents"]:
            self.issue(agent["id"], agent["credentials"])
        for shared in scenario.get("shared_credentials", []):
            self.shared(shared["name"], shared["type"], shared["template"])
