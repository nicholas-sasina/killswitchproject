"""support-desk-attack.yaml must be a STRICT SUPERSET of support-desk.yaml:
the same agents, shared credentials, customers, and every existing ticket and
file unchanged, plus exactly one poisoned ticket and one planted file. That is
what keeps burn_api/scripts/e2e.py (agent-reader) passing with it loaded."""
from pathlib import Path

import yaml

SCEN = Path(__file__).resolve().parents[1] / "scenarios"
BASE = yaml.safe_load((SCEN / "support-desk.yaml").read_text())
ATTACK = yaml.safe_load((SCEN / "support-desk-attack.yaml").read_text())

POISONED_TICKET = "T-1099"
PLANTED_FILE = "config/app.env.bak"


def test_agents_shared_credentials_and_customers_are_unchanged():
    assert ATTACK["agents"] == BASE["agents"]
    assert ATTACK.get("shared_credentials") == BASE.get("shared_credentials")
    assert ATTACK["customers"] == BASE["customers"]


def test_tickets_are_the_base_set_plus_only_the_poisoned_one():
    attack_by_id = {t["id"]: t for t in ATTACK["tickets"]}
    for ticket in BASE["tickets"]:
        assert attack_by_id[ticket["id"]] == ticket  # unchanged, field for field
    base_ids = {t["id"] for t in BASE["tickets"]}
    added = [t["id"] for t in ATTACK["tickets"] if t["id"] not in base_ids]
    assert added == [POISONED_TICKET]


def test_files_are_the_base_set_plus_only_the_planted_one():
    attack_by_path = {f["path"]: f for f in ATTACK["files"]}
    for file in BASE["files"]:
        assert attack_by_path[file["path"]] == file  # unchanged
    base_paths = {f["path"] for f in BASE["files"]}
    added = [f["path"] for f in ATTACK["files"] if f["path"] not in base_paths]
    assert added == [PLANTED_FILE]


def test_only_the_poisoned_ticket_points_at_the_planted_canary_file():
    planted = next(f for f in ATTACK["files"] if f["path"] == PLANTED_FILE)
    assert "{{canary}}" in planted["content"]
    # No honest ticket sends a reader to the backup; only the poisoned one does.
    for ticket in BASE["tickets"]:
        assert PLANTED_FILE not in ticket["body"]
    poisoned = next(t for t in ATTACK["tickets"] if t["id"] == POISONED_TICKET)
    assert PLANTED_FILE in poisoned["body"]
