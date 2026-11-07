"""AWS clients and settings for the minter, created lazily and cached.

Lazy so unit tests can start a mock first; reset() drops the cache between tests.
"""
import functools
import os

import boto3


@functools.cache
def secrets():
    return boto3.client("secretsmanager")


@functools.cache
def dynamodb():
    return boto3.resource("dynamodb")


@functools.cache
def iam():
    return boto3.client("iam")


@functools.cache
def cloudtrail():
    return boto3.client("cloudtrail")


def table(name: str):
    return dynamodb().Table(name)


def setting(name: str) -> str:
    """Deploy-time setting from the Lambda environment (set by the CDK stack)."""
    return os.environ[name]


def reset() -> None:
    for fn in (secrets, dynamodb, iam, cloudtrail):
        fn.cache_clear()
