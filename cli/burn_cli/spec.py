"""burn.yaml: the roles a team keeps in code. Validation, the starter template, and the API mapping."""
import json
from importlib import resources
from pathlib import Path

import jsonschema
import yaml

DEFAULT_FILE = "burn.yaml"
ROLE_KEYS = ("name", "owner", "tools", "credentials", "may_message", "may_spawn", "egress")

TEMPLATE = """\
# Roles for `burn apply`. Join tokens and instances are created at run time.
# A spawned child gets its role's tools, credentials and egress narrowed to what its parent has.
version: 1
roles:
  - name: planner
    owner: my-team
    tools: [read_ticket, send_message, notify]
    credentials: [api_key]
    may_message: [worker]
    may_spawn: [worker]
  - name: worker
    owner: my-team
    tools: [read_ticket, notify]
    credentials: [api_key]
    may_message: [planner]
"""


class SpecError(Exception):
    pass


def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(resources.files("burn_cli").joinpath("burn.schema.json").read_text())
    return jsonschema.Draft202012Validator(schema)


def validate(doc) -> list[str]:
    """Every problem in a parsed burn.yaml, as 'path: message' lines. Empty when it is valid."""
    problems = [f"{'/'.join(map(str, e.absolute_path)) or 'burn.yaml'}: {e.message}"
                for e in sorted(_validator().iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))]
    if not problems:
        names = [r["name"] for r in doc["roles"]]
        problems += [f"roles: duplicate role name {n!r}" for n in sorted({n for n in names if names.count(n) > 1})]
    return problems


def load(path: Path) -> dict:
    """Parse and validate a burn.yaml; raises SpecError listing every problem."""
    try:
        doc = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise SpecError(f"cannot read {path}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise SpecError(f"{path} is not valid YAML: {exc}") from None
    problems = validate(doc)
    if problems:
        raise SpecError(f"{path} is not valid:\n" + "\n".join(f"  {p}" for p in problems))
    return doc


def role_body(role: dict) -> dict:
    """The PUT /roles/{name} body for one burn.yaml role."""
    return {**role, "managed_by": "code"}


def from_api(roles: list[dict]) -> dict:
    """A burn.yaml document for roles read from the API. Fields with no value are left out."""
    return {"version": 1, "roles": [
        {k: role[k] for k in ROLE_KEYS if k == "tools" or role.get(k) not in (None, [])}
        for role in sorted(roles, key=lambda r: r["name"])]}


def dump(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=None)
