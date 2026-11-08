"""init, apply, pull, token create, ps and kill against the fake API."""
import json
from pathlib import Path

import pytest
import yaml

from burn_cli import spec
from burn_cli.main import run
from fake_api import API_TOKEN

SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / "burn.schema.json"
EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "burn.yaml"


def burn(capsys, *argv):
    code = run(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_packaged_schema_matches_the_contract():
    assert (Path(spec.__file__).parent / "burn.schema.json").read_text() == SCHEMA.read_text()


def test_contract_example_is_valid():
    assert spec.validate(yaml.safe_load(EXAMPLE.read_text())) == []


def test_init_writes_a_valid_template(tmp_path, capsys):
    code, out, _ = burn(capsys, "init", "-f", str(tmp_path / "burn.yaml"))
    assert code == 0 and spec.validate(yaml.safe_load((tmp_path / "burn.yaml").read_text())) == []


def test_init_does_not_overwrite_without_force(tmp_path, capsys):
    target = tmp_path / "burn.yaml"
    target.write_text("keep")
    code, _, err = burn(capsys, "init", "-f", str(target))
    assert code == 2 and "already exists" in err and target.read_text() == "keep"
    assert burn(capsys, "init", "-f", str(target), "--force")[0] == 0


def write_yaml(tmp_path, doc):
    path = tmp_path / "burn.yaml"
    path.write_text(yaml.safe_dump(doc))
    return str(path)


def test_apply_puts_each_role_managed_by_code(fake, tmp_path, capsys):
    code, out, _ = burn(capsys, "apply", "-f", str(EXAMPLE))
    assert code == 0 and "planner" in out and "worker" in out
    puts = fake.calls("PUT", "/roles/worker")
    assert puts[0][2]["managed_by"] == "code" and puts[0][2]["may_message"] == ["planner"]
    assert {r["name"] for r in fake.roles.values()} == {"planner", "worker"}


def test_apply_validates_before_calling_the_api(fake, tmp_path, capsys):
    path = write_yaml(tmp_path, {"version": 1, "roles": [
        {"name": "ok", "tools": []}, {"name": "Bad Name", "tools": []}]})
    code, _, err = burn(capsys, "apply", "-f", path)
    assert code == 2 and "Bad Name" in err
    assert not [r for r in fake.requests if r[0] == "PUT"]


def test_apply_rejects_duplicates_and_unknown_fields(fake, tmp_path, capsys):
    path = write_yaml(tmp_path, {"version": 1, "roles": [
        {"name": "a", "tools": [], "color": "red"}, {"name": "a", "tools": []}]})
    code, _, err = burn(capsys, "apply", "-f", path)
    assert code == 2 and "color" in err and fake.requests == []
    path = write_yaml(tmp_path, {"version": 1, "roles": [{"name": "a", "tools": []}, {"name": "a", "tools": []}]})
    assert "duplicate role name 'a'" in burn(capsys, "apply", "-f", path)[2]


def test_apply_reports_a_role_managed_elsewhere(fake, tmp_path, capsys):
    fake.roles["worker"] = {"name": "worker", "tools": [], "managed_by": "ui"}
    code, _, err = burn(capsys, "apply", "-f", str(EXAMPLE))
    assert code == 1 and "HTTP 409" in err


def test_apply_missing_file_is_a_usage_error(fake, capsys):
    assert burn(capsys, "apply", "-f", "nope.yaml")[0] == 2


def test_pull_writes_only_dashboard_roles_and_they_round_trip(fake, tmp_path, capsys):
    fake.roles.update({
        "triage": {"name": "triage", "tools": ["read_ticket"], "credentials": ["api_key"], "may_message": [],
                   "may_spawn": [], "egress": None, "owner": None, "managed_by": "ui", "active_instances": 3,
                   "created_at": "t"},
        "planner": {"name": "planner", "tools": ["notify"], "managed_by": "code"},
        "reader": {"name": "reader", "tools": ["read_ticket"], "managed_by": "scenario"}})
    code, out, _ = burn(capsys, "pull")
    assert code == 0 and "triage" in out
    doc = yaml.safe_load(Path("burn.yaml").read_text())
    assert doc == {"version": 1, "roles": [{"name": "triage", "tools": ["read_ticket"], "credentials": ["api_key"]}]}
    assert spec.validate(doc) == []
    assert burn(capsys, "apply")[0] == 1  # the dashboard owns it until apply switches it: API says 409


def test_pull_with_nothing_to_pull_writes_no_file(fake, capsys):
    fake.roles["planner"] = {"name": "planner", "tools": [], "managed_by": "code"}
    code, out, _ = burn(capsys, "pull")
    assert code == 0 and "no dashboard-made roles" in out and not Path("burn.yaml").exists()


def test_token_create_prints_the_token_once_with_a_join_command(fake, capsys):
    fake.roles["worker"] = {"name": "worker", "tools": [], "managed_by": "code"}
    code, out, _ = burn(capsys, "token", "create", "worker", "--uses", "5", "--ttl", "600")
    assert fake.calls("POST", "/roles/worker/tokens")[0][2] == {"uses": 5, "ttl": 600}
    token = next(line for line in out.splitlines() if line.startswith("bjt_"))
    assert code == 0 and out.count(token) == 1  # printed exactly once, on its own line
    assert API_TOKEN not in out
    # the join commands it suggests carry no token, so they cannot land in shell history
    assert "burn run --role worker --token-stdin" in out and "read -rs BURN_JOIN_TOKEN" in out
    assert not [ln for ln in out.splitlines() if "burn run" in ln and "bjt_" in ln]


def test_token_create_unknown_role(fake, capsys):
    assert burn(capsys, "token", "create", "ghost")[0] == 1


def seed_instances(fake):
    fake.roles["planner"] = {"name": "planner", "tools": [], "may_spawn": ["worker"], "managed_by": "code"}
    fake.roles["worker"] = {"name": "worker", "tools": [], "managed_by": "code"}
    fake.add_instance("sess_p", "planner", token_id="tok1")
    fake.add_instance("sess_w1", "worker", parent="sess_p")
    fake.add_instance("sess_w2", "worker", parent="sess_p", status="revoked")
    fake.add_instance("sess_w3", "worker", parent="sess_w1")


def test_ps_groups_by_role_and_shows_lineage(fake, capsys):
    seed_instances(fake)
    code, out, _ = burn(capsys, "ps")
    assert code == 0
    assert "planner (1)" in out and "worker (3)" in out
    assert "  sess_w3" in out and "sess_w1" in out  # a grandchild is indented under its parent chain


def test_ps_filters_are_sent_to_the_api(fake, capsys):
    seed_instances(fake)
    out = burn(capsys, "ps", "--role", "worker", "--status", "active")[1]
    assert fake.requests[-1][1] == "/instances?role=worker&status=active"
    assert "sess_w1" in out and "sess_w2" not in out and "planner" not in out
    assert "no instances" in burn(capsys, "ps", "--role", "nobody")[1]


@pytest.mark.parametrize("argv,body", [
    (["sess_w1"], {"scope": "instance", "id": "sess_w1"}),
    (["--role", "worker"], {"scope": "role", "id": "worker"}),
    (["--lineage", "sess_p"], {"scope": "lineage", "id": "sess_p"}),
    (["--token", "tok1"], {"scope": "token", "id": "tok1"}),
])
def test_kill_scopes_post_to_burns(fake, capsys, argv, body):
    seed_instances(fake)
    code, out, _ = burn(capsys, "kill", *argv, "--yes")
    assert code == 0 and "burned" in out
    assert fake.calls("POST", "/burns")[0][2] == body


def test_kill_lineage_burns_descendants(fake, capsys):
    seed_instances(fake)
    out = burn(capsys, "kill", "--lineage", "sess_p", "--yes")[1]
    assert "burned 4 instance(s)" in out
    assert {i["status"] for i in fake.instances.values()} == {"revoked"}


def test_kill_agent_id_still_uses_the_single_agent_route(fake, capsys):
    code, out, _ = burn(capsys, "kill", "agent-reader", "--yes", "--reason", "test")
    assert code == 0 and fake.calls("POST", "/burn")[0][2] == {"agent_id": "agent-reader", "reason": "test"}


def test_kill_asks_for_confirmation(fake, capsys, monkeypatch):
    seed_instances(fake)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code, out, _ = burn(capsys, "kill", "--role", "worker")
    assert code == 1 and "aborted" in out and not fake.calls("POST", "/burns")


def test_kill_needs_a_target(fake, capsys):
    with pytest.raises(SystemExit) as exc:
        run(["kill", "--yes"])
    assert exc.value.code == 2


def test_kill_nothing_matching_is_an_api_error(fake, capsys):
    assert burn(capsys, "kill", "--role", "ghost", "--yes")[0] == 1


def test_the_api_token_is_never_printed(fake, capsys):
    seed_instances(fake)
    for argv in (["ps"], ["apply", "-f", str(EXAMPLE)], ["kill", "--role", "worker", "--yes"], ["--json", "ps"]):
        _, out, err = burn(capsys, *argv)
        assert API_TOKEN not in out + err
