"""Shared fixtures: moto-backed AWS services.

Every credential-like value in these tests is random and generated at run time.
"""
import os

import pytest
from moto import mock_aws

os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1",
})

import badges  # noqa: E402


def _reset_caches():
    for cached in (badges._cognito, badges.identity_config):
        cached.cache_clear()


@pytest.fixture
def aws():
    with mock_aws():
        _reset_caches()
        yield
        _reset_caches()
