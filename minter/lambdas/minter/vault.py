"""Secrets Manager wrapper: the only place credential VALUES are written.

Rotation relies on Secrets Manager keeping the previous value at AWSPREVIOUS
whenever a new value is put, so nothing else has to remember old values.
"""
import aws


def put(secret_ref: str, value: str) -> None:
    """Create the secret, or add a new current version if it already exists."""
    try:
        aws.secrets().create_secret(Name=secret_ref, SecretString=value)
    except aws.secrets().exceptions.ResourceExistsException:
        aws.secrets().put_secret_value(SecretId=secret_ref, SecretString=value)


def get(secret_ref: str, stage: str = "AWSCURRENT") -> str | None:
    try:
        return aws.secrets().get_secret_value(SecretId=secret_ref, VersionStage=stage)["SecretString"]
    except aws.secrets().exceptions.ResourceNotFoundException:
        return None


def exists(secret_ref: str) -> bool:
    try:
        aws.secrets().describe_secret(SecretId=secret_ref)
        return True
    except aws.secrets().exceptions.ResourceNotFoundException:
        return False


def delete(secret_ref: str) -> None:
    """Delete immediately (no recovery window). Succeeds quietly if it is already gone."""
    aws.secrets().delete_secret(SecretId=secret_ref, ForceDeleteWithoutRecovery=True)
