"""Reset deletes Burn's own badge clients, orphans included, and nothing else."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from reset import is_burn_badge, remove_badges  # noqa: E402


class FakeCognito:
    def __init__(self, clients):
        self.clients, self.deleted = clients, []

    def list_user_pool_clients(self, UserPoolId, MaxResults, NextToken=None):
        names = list(self.clients.items())
        start = int(NextToken or 0)
        page = names[start:start + 2]  # tiny pages to exercise pagination
        out = {"UserPoolClients": [{"ClientId": i, "ClientName": n} for i, n in page]}
        if start + 2 < len(names):
            out["NextToken"] = str(start + 2)
        return out

    def delete_user_pool_client(self, UserPoolId, ClientId):
        self.deleted.append(ClientId)


CLIENTS = {
    "c1": "burn-agent-reader-sess_aa",
    "c2": "burn-agent-notifier-sess_bb",
    "c3": "burn-agent-eval-7-sess_cc",   # load-test badge: protected
    "c4": "my-other-app",                # not Burn's
    "c5": "burn-agent-analyst-sess_dd",
}


def test_name_filter():
    assert is_burn_badge("burn-agent-reader-sess_aa")
    assert not is_burn_badge("burn-agent-eval-7-sess_cc")
    assert not is_burn_badge("my-other-app")
    assert not is_burn_badge("burn-agent-reader")  # no session marker


def test_reset_removes_every_burn_badge_but_not_eval_or_foreign_clients():
    cognito = FakeCognito(dict(CLIENTS))
    names = remove_badges(cognito, "pool", keep_client_ids=set())
    assert sorted(cognito.deleted) == ["c1", "c2", "c5"]
    assert len(names) == 3


def test_orphans_only_keeps_clients_of_existing_sessions():
    cognito = FakeCognito(dict(CLIENTS))
    remove_badges(cognito, "pool", keep_client_ids={"c1", "c3"})
    assert sorted(cognito.deleted) == ["c2", "c5"]
