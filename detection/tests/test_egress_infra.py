"""Run with the infra environment; no deployment or AWS calls."""
import sys
from pathlib import Path

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "infra"))
from burn_infra.egress import BurnEgressStack


def test_egress_identity_has_only_the_required_permissions():
    app = cdk.App()
    stack = BurnEgressStack(app, "BurnEgress", env=cdk.Environment(region="us-east-1"))
    template = Template.from_stack(stack)
    template.resource_count_is("AWS::IAM::User", 1)
    template.resource_count_is("AWS::IAM::AccessKey", 1)
    template.resource_count_is("AWS::SecretsManager::Secret", 1)
    resources = template.to_json()["Resources"]
    permissions = []
    for resource in resources.values():
        if resource["Type"] == "AWS::IAM::Policy":
            for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
                actions = statement["Action"]
                actions = [actions] if isinstance(actions, str) else actions
                assert all("*" not in action for action in actions)
                assert statement["Resource"] != "*"
                permissions.extend(actions)
    assert set(permissions) == {"dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem",
                                "dynamodb:PutItem", "secretsmanager:GetSecretValue"}
    secret = next(r for r in resources.values() if r["Type"] == "AWS::SecretsManager::Secret")
    assert secret["Properties"]["Name"] == "burn-system/egress-proxy-key"
    assert "Fn::GetAtt" in str(secret["Properties"]["SecretString"])
