"""The internet-facing provider can invoke only the Burn API's canary alias."""
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from burn_infra.burn_api import BurnApiStack  # noqa: E402
from burn_infra.minter import BurnMinterStack  # noqa: E402
from burn_contracts import names  # noqa: E402  (path added by burn_infra)

ENV = cdk.Environment(account="123456789012", region="us-east-1")


@pytest.fixture(scope="module")
def minter():
    return Template.from_stack(BurnMinterStack(cdk.App(), "BurnMinter", env=ENV))


def _provider(template: Template) -> dict:
    return next(f["Properties"] for f in template.find_resources("AWS::Lambda::Function").values()
                if f["Properties"].get("FunctionName") == "burn-provider")


def _provider_invoke_targets(template: Template) -> list[str]:
    role_id = _provider(template)["Role"]["Fn::GetAtt"][0]
    targets = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        if {"Ref": role_id} not in policy["Properties"]["Roles"]:
            continue
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
            if "lambda:InvokeFunction" in actions:
                resources = statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]]
                targets += [r if isinstance(r, str) else "".join(p if isinstance(p, str) else "<token>"
                                                                  for p in r["Fn::Join"][1]) for r in resources]
    return targets


def test_the_provider_may_invoke_only_the_api_alias_and_the_minter(minter):
    targets = _provider_invoke_targets(minter)
    assert len(targets) == 2
    assert any(t.endswith(f":function:{names.BURN_API_FUNCTION}:{names.BURN_API_CANARY_ALIAS}") for t in targets)
    assert any(t.endswith(f":function:{names.MINTER_FUNCTION}") for t in targets)
    assert not any(t.endswith(f":function:{names.BURN_API_FUNCTION}") for t in targets)  # never unqualified
    assert not any("*" in t for t in targets)


def test_the_provider_targets_the_alias(minter):
    assert _provider(minter)["Environment"]["Variables"]["BURN_API_FUNCTION"] == names.BURN_API_CANARY_TARGET


@pytest.mark.parametrize("flag", [None, "on"])
def test_the_api_has_a_canary_alias_on_a_published_version(flag):
    app = cdk.App(context={"burn:people_auth": flag} if flag else {})
    api = Template.from_stack(BurnApiStack(app, "BurnApi", env=ENV))
    api.resource_count_is("AWS::Lambda::Alias", 1)
    api.has_resource_properties("AWS::Lambda::Alias", {"Name": names.BURN_API_CANARY_ALIAS})
    api.resource_count_is("AWS::Lambda::Version", 1)
    # API Gateway still invokes the unqualified function, not the alias
    integration = next(iter(api.find_resources("AWS::ApiGatewayV2::Integration").values()))["Properties"]
    assert "Alias" not in str(integration["IntegrationUri"])
