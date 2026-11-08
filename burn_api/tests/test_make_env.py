import sys
import secrets
from unittest.mock import MagicMock

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1] / "scripts"))
import make_env  # noqa: E402


def test_missing_values_are_blank_not_errors():
    ssm, secrets, sts = MagicMock(), MagicMock(), MagicMock()
    ssm.get_parameter.side_effect = lambda Name: (
        {"Parameter": {"Value": "https://api.example.invalid"}} if Name == "/burn/api/url" else (_ for _ in ()).throw(KeyError(Name)))
    secrets.get_secret_value.side_effect = RuntimeError("denied")
    sts.get_caller_identity.return_value = {"Account": "000000000000"}
    values = make_env.collect(ssm, secrets, sts)
    assert values["BURN_API_URL"] == "https://api.example.invalid"
    assert values["BURN_DEMO_TOKEN"] == "" and values["BURN_GATEWAY_URL"] == ""
    text = make_env.render(values, ["BURN_API_URL", "BURN_DEMO_TOKEN"])
    assert "BURN_API_URL=https://api.example.invalid" in text
    assert "# BURN_DEMO_TOKEN is not available yet" in text and "\nBURN_DEMO_TOKEN=\n" in text


def test_render_renames_for_vite():
    text = make_env.render({"BURN_API_URL": "https://x.invalid"}, ["BURN_API_URL"], {"BURN_API_URL": "VITE_BURN_API_URL"})
    assert text.startswith("VITE_BURN_API_URL=https://x.invalid")


def test_dashboard_env_contains_only_burn_connection(tmp_path, monkeypatch, capsys):
    token = secrets.token_hex(24)
    monkeypatch.setattr(make_env, "REPO", tmp_path)
    monkeypatch.setattr(make_env.boto3, "client", lambda *args, **kwargs: object())
    monkeypatch.setattr(make_env, "collect", lambda *args: {
        "BURN_API_URL": "https://api.example.invalid", "BURN_DEMO_TOKEN": token,
    })
    make_env.main()
    lines = [line for line in (tmp_path / "dashboard" / ".env.local").read_text().splitlines()
             if not line.startswith("#")]
    assert {line.split("=", 1)[0] for line in lines} == {
        "VITE_BURN_API_URL", "VITE_BURN_PEOPLE_DOMAIN", "VITE_BURN_PEOPLE_CLIENT_ID", "VITE_BURN_DEMO_TOKEN"}
    assert token not in capsys.readouterr().out


PEOPLE = {"BURN_API_URL": "https://api.example.invalid", "BURN_DEMO_TOKEN": "demo-" + secrets.token_hex(8),
          "BURN_PEOPLE_DOMAIN": "https://burn-people-x.auth.us-east-1.amazoncognito.com",
          "BURN_PEOPLE_CLIENT_ID": "clientid123", "BURN_PEOPLE_USER_POOL_ID": "us-east-1_abc",
          "BURN_PEOPLE_ISSUER": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc"}


def test_with_sign_in_on_the_dashboard_gets_the_people_values_and_no_admin_token():
    text = make_env.dashboard_env(PEOPLE | {"BURN_PEOPLE_ENABLED": "on"})
    assert "VITE_BURN_PEOPLE_DOMAIN=https://burn-people-x.auth.us-east-1.amazoncognito.com\n" in text
    assert "VITE_BURN_PEOPLE_CLIENT_ID=clientid123\n" in text
    assert "DEMO_TOKEN" not in text and PEOPLE["BURN_DEMO_TOKEN"] not in text


def test_with_sign_in_off_the_dashboard_still_gets_the_admin_token():
    for enabled in ("off", ""):
        text = make_env.dashboard_env(PEOPLE | {"BURN_PEOPLE_ENABLED": enabled})
        assert f"VITE_BURN_DEMO_TOKEN={PEOPLE['BURN_DEMO_TOKEN']}\n" in text
        assert "VITE_BURN_PEOPLE_CLIENT_ID=clientid123\n" in text


def test_the_people_values_come_from_their_ssm_parameters():
    from burn_contracts import names
    ssm, sm, sts = MagicMock(), MagicMock(), MagicMock()
    published = {names.PEOPLE_USER_POOL_ID_PARAM: "pool", names.PEOPLE_CLIENT_ID_PARAM: "client",
                 names.PEOPLE_DOMAIN_PARAM: "https://d.example", names.PEOPLE_ISSUER_PARAM: "https://i.example",
                 names.PEOPLE_ENABLED_PARAM: "on"}
    ssm.get_parameter.side_effect = lambda Name: {"Parameter": {"Value": published[Name]}} if Name in published \
        else (_ for _ in ()).throw(KeyError(Name))
    values = make_env.collect(ssm, sm, sts)
    assert [values[k] for k in make_env.PEOPLE_KEYS] == ["pool", "client", "https://d.example", "https://i.example", "on"]
