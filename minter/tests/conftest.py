"""Shared fixtures: every test runs against moto (in-memory AWS) with the real table layout."""
import json
import secrets

import boto3
import pytest
from moto import mock_aws

import aws
import provider_app

BOUNDARY_ARN = "arn:aws:iam::123456789012:policy/burn-agent-boundary"
AGENT_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:us-east-1:123456789012:inference-profile/example-profile"]},
        {"Effect": "Allow", "Action": "s3:ListAllMyBuckets", "Resource": "*"},
    ],
}


def _create_tables(ddb) -> None:
    def make(name, pk, gsi=None):
        kwargs = {}
        attrs = [{"AttributeName": pk, "AttributeType": "S"}]
        if gsi:
            attrs.append({"AttributeName": gsi[1], "AttributeType": "S"})
            kwargs["GlobalSecondaryIndexes"] = [{
                "IndexName": gsi[0], "KeySchema": [{"AttributeName": gsi[1], "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}}]
        ddb.create_table(TableName=name, AttributeDefinitions=attrs, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}], **kwargs)

    make("burn-credentials", "secret_ref", ("by_owner", "owner_agent_id"))
    make("burn-fingerprints", "fingerprint", ("by_secret", "secret_ref"))
    ddb.create_table(
        TableName="burn-events", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "stream", "AttributeType": "S"},
                              {"AttributeName": "event_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "stream", "KeyType": "HASH"},
                   {"AttributeName": "event_id", "KeyType": "RANGE"}])


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("PROVIDER_HOST", "provider.example")
    monkeypatch.setenv("BOUNDARY_POLICY_ARN", BOUNDARY_ARN)
    monkeypatch.setenv("AGENT_POLICY_JSON", json.dumps(AGENT_POLICY))
    with mock_aws():
        aws.reset()
        provider_app._key_cache.clear()
        _create_tables(boto3.client("dynamodb"))
        boto3.client("secretsmanager").create_secret(
            Name="burn-system/fingerprint-key", SecretString=secrets.token_hex(32))
        yield
        aws.reset()


@pytest.fixture
def secrets_client():
    return boto3.client("secretsmanager")


@pytest.fixture
def tables():
    ddb = boto3.resource("dynamodb")
    return {name: ddb.Table(f"burn-{name}") for name in ("credentials", "fingerprints", "events")}
