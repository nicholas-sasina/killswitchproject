"""make deploy's flags follow the account: sign-in and the hosted dashboard never switch off by accident."""
import pathlib
import sys

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from burn_contracts import names

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))
import deploy_flags  # noqa: E402
from hosted_dashboard import Refused  # noqa: E402

HOSTED = "https://d111111abcdef8.cloudfront.net"
SIGN_IN_ON = {names.PEOPLE_ENABLED_PARAM: "on"}


class FakeSsm:
    def __init__(self, values: dict[str, str], error: Exception | None = None):
        self.values, self.error = values, error

    def get_parameter(self, Name):
        if self.error:
            raise self.error
        if Name not in self.values:
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return {"Parameter": {"Value": self.values[Name]}}


def answers(status):
    fetched = []

    def fetch(url):
        fetched.append(url)
        return status
    fetch.fetched = fetched
    return fetch


def flags(people, hosted):
    return {"burn:people_auth": people, "burn:dashboard_hosted": hosted}


def never_fetched(url):
    raise AssertionError(f"fetched {url}")


@pytest.mark.parametrize("values,expected", [
    ({}, flags("off", "off")),                                              # a new account: nothing deployed
    ({names.PEOPLE_ENABLED_PARAM: "off"}, flags("off", "off")),
    ({names.PEOPLE_ENABLED_PARAM: "off", names.DASHBOARD_URL_PARAM: HOSTED}, flags("off", "off")),
    (SIGN_IN_ON, flags("on", "off")),                                       # sign-in on, nothing hosted
])
def test_follows_the_account_without_fetching_when_there_is_nothing_to_check(values, expected):
    assert deploy_flags.decide(FakeSsm(values), fetch=never_fetched) == expected


def test_a_live_hosted_dashboard_stays_hosted():
    fetch = answers(200)
    assert deploy_flags.decide(FakeSsm({**SIGN_IN_ON, names.DASHBOARD_URL_PARAM: HOSTED}), fetch=fetch) == flags(
        "on", "on")
    assert fetch.fetched == [HOSTED + "/"]


@pytest.mark.parametrize("status", [403, 404])
def test_an_empty_distribution_is_not_hosted(status):
    # make deploy creates BurnDashboard, and its address, before make dashboard-deploy uploads anything.
    ssm = FakeSsm({**SIGN_IN_ON, names.DASHBOARD_URL_PARAM: HOSTED})
    assert deploy_flags.decide(ssm, fetch=answers(status)) == flags("on", "off")


@pytest.mark.parametrize("status", [None, 500, 503])
def test_no_answer_refuses_rather_than_dropping_the_hosted_dashboard(status):
    ssm = FakeSsm({**SIGN_IN_ON, names.DASHBOARD_URL_PARAM: HOSTED})
    with pytest.raises(Refused, match="DASHBOARD_HOSTED=on or DASHBOARD_HOSTED=off"):
        deploy_flags.decide(ssm, fetch=answers(status))


def test_overrides_change_one_on_purpose():
    ssm = FakeSsm({**SIGN_IN_ON, names.DASHBOARD_URL_PARAM: HOSTED})
    assert deploy_flags.decide(ssm, people_auth="off", fetch=never_fetched) == flags("off", "off")
    assert deploy_flags.decide(ssm, dashboard_hosted="off", fetch=never_fetched) == flags("on", "off")
    assert deploy_flags.decide(ssm, dashboard_hosted="on", fetch=never_fetched) == flags("on", "on")
    # The sign-in switch itself: nothing is hosted yet.
    assert deploy_flags.decide(FakeSsm({}), people_auth="on", fetch=never_fetched) == flags("on", "off")


def test_hosting_without_an_address_is_refused():
    with pytest.raises(Refused, match="run make dashboard-deploy first"):
        deploy_flags.decide(FakeSsm(SIGN_IN_ON), dashboard_hosted="on", fetch=never_fetched)


def test_main_prints_the_flags_for_cdk(monkeypatch, capsys):
    monkeypatch.setattr(deploy_flags.boto3, "client", lambda *a, **k: FakeSsm(SIGN_IN_ON))
    assert deploy_flags.main([]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "-c burn:people_auth=on -c burn:dashboard_hosted=off"
    assert "deploying with burn:people_auth=on, burn:dashboard_hosted=off" in out.err


@pytest.mark.parametrize("error", [NoCredentialsError(),
                                   ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetParameter")])
def test_main_refuses_when_it_cannot_read_the_account(monkeypatch, capsys, error):
    monkeypatch.setattr(deploy_flags.boto3, "client", lambda *a, **k: FakeSsm({}, error))
    assert deploy_flags.main([]) == 1
    out = capsys.readouterr()
    assert out.out == ""  # nothing for make to pass on
    assert "cannot read this account's sign-in state" in out.err


def test_main_rejects_anything_but_on_or_off():
    with pytest.raises(SystemExit):
        deploy_flags.main(["--people-auth", "yes"])
