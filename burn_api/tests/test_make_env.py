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
    lines = (tmp_path / "dashboard" / ".env.local").read_text().splitlines()
    assert {line.split("=", 1)[0] for line in lines} == {"VITE_BURN_API_URL", "VITE_BURN_DEMO_TOKEN"}
    assert token not in capsys.readouterr().out
