import json
import httpx
import pytest

from quota_capture import capture
from throttling import response_evidence


@pytest.mark.parametrize("headers,body,expected", [
    ({"x-amzn-errortype": "ThrottlingException:detail"}, {}, "ThrottlingException"),
    ({}, {"__type": "namespace#TooManyRequestsException"}, "TooManyRequestsException"),
    ({"x-amzn-errortype": "private-payload"}, {"message": "private-payload"}, "unidentified"),
])
def test_only_known_error_labels_survive(headers, body, expected):
    result = response_evidence(httpx.Response(429, headers=headers, json=body))
    assert result["error_type"] == expected
    assert "private-payload" not in json.dumps(result)
    assert response_evidence(httpx.Response(200, json=body)) is None


def test_quota_snapshot_pages_and_does_not_claim_causality_or_retain_arns():
    class Quotas:
        calls = []
        def list_service_quotas(self, **kwargs):
            self.calls.append(kwargs)
            result = {"Quotas": [{"QuotaName": "Invocation requests", "QuotaCode": "L-EXAMPLE",
                                  "Value": 10, "Unit": "Count/Second", "QuotaArn": "private-arn"}]}
            if "NextToken" not in kwargs:
                result["NextToken"] = "private-pagination-token"
            return result
    client = Quotas()
    result = capture(client, "example-service")
    assert client.calls == [{"ServiceCode": "example-service"},
                            {"ServiceCode": "example-service", "NextToken": "private-pagination-token"}]
    assert result["causal_attribution"] == "unverified" and len(result["quotas"]) == 2
    assert "private-" not in json.dumps(result)


@pytest.mark.parametrize("service", ["", "../private", "service key", None])
def test_invalid_service_codes_fail_before_contacting_aws(service):
    class NoCalls:
        def list_service_quotas(self, **kwargs):
            pytest.fail("must not contact AWS")
    with pytest.raises(ValueError):
        capture(NoCalls(), service)
