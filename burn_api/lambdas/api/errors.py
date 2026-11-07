"""Errors the router turns into HTTP responses."""
from botocore.exceptions import ClientError

# AWS error codes that mean "slow down", not "broken".
THROTTLE_CODES = frozenset({
    "Throttling", "ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded",
    "ProvisionedThroughputExceededException", "ThrottledException",
})
RETRY_AFTER_SECONDS = 2


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Throttled(Exception):
    """A dependency (IAM, Cognito, the minter) asked us to back off."""


def is_throttle(exc: BaseException) -> bool:
    if isinstance(exc, Throttled):
        return True
    return isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in THROTTLE_CODES
