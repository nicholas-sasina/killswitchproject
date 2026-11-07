import sys
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
