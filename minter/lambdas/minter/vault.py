"""Secrets Manager wrapper: the only place credential VALUES are written.

Rotation relies on Secrets Manager keeping the previous value at AWSPREVIOUS
whenever a new value is put, so nothing else has to remember old values.
"""
import time

import aws

# Names are reused right after a demo reset. Our own deletes skip the recovery window,
# but Secrets Manager can still refuse the name for a few seconds, and secrets deleted
# some other way may sit in a recovery window for days. Both are handled in put().
RECREATE_ATTEMPTS = 10
RECREATE_WAIT_SECONDS = 2


def _create_or_update(sm, secret_ref: str, value: str) -> None:
    try:
        sm.create_secret(Name=secret_ref, SecretString=value)
    except sm.exceptions.ResourceExistsException:
        if "DeletedDate" in sm.describe_secret(SecretId=secret_ref):
            sm.restore_secret(SecretId=secret_ref)  # in a recovery window: bring it back first
        sm.put_secret_value(SecretId=secret_ref, SecretString=value)


def put(secret_ref: str, value: str) -> None:
    """Create the secret, or add a new current version if it already exists."""
    sm = aws.secrets()
    for attempt in range(RECREATE_ATTEMPTS):
        try:
            return _create_or_update(sm, secret_ref, value)
        except sm.exceptions.InvalidRequestException as err:
            if "deletion" not in str(err) and "deleted" not in str(err):
                raise
            if attempt == RECREATE_ATTEMPTS - 1:
                raise
        try:  # in a recovery window: bring it back, then give it the new value
            sm.restore_secret(SecretId=secret_ref)
            return _create_or_update(sm, secret_ref, value)
        except (sm.exceptions.InvalidRequestException, sm.exceptions.ResourceNotFoundException):
            time.sleep(RECREATE_WAIT_SECONDS)  # already being deleted for good: wait it out


def get(secret_ref: str, stage: str = "AWSCURRENT") -> str | None:
    try:
        return aws.secrets().get_secret_value(SecretId=secret_ref, VersionStage=stage)["SecretString"]
    except (aws.secrets().exceptions.ResourceNotFoundException,
            aws.secrets().exceptions.InvalidRequestException):  # InvalidRequest = scheduled for deletion
        return None


def exists(secret_ref: str) -> bool:
    try:
        return "DeletedDate" not in aws.secrets().describe_secret(SecretId=secret_ref)
    except aws.secrets().exceptions.ResourceNotFoundException:
        return False


def delete(secret_ref: str) -> None:
    """Delete immediately (no recovery window) so the name can be reused. Quiet if already gone.

    Only ever called with burn/... names the minter created; the IAM policy enforces the prefix.
    """
    aws.secrets().delete_secret(SecretId=secret_ref, ForceDeleteWithoutRecovery=True)
