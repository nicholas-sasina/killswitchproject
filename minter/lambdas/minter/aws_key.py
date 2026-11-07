"""Per-agent AWS keys: a real IAM user with one narrow inline policy.

IAM is eventually consistent: a freshly created access key can take roughly
5-15 seconds to be accepted by AWS. The minter does not sleep (that would burn
Lambda time); callers that use the key right away should retry for a few seconds.

Guardrail: the user is created with the `burn-agent-boundary` permissions
boundary, and the minter's own IAM role only allows creating users that carry
it. Even a bug here cannot mint a user more powerful than the boundary.
"""
import json

import aws
import fingerprints
import registry
import vault
from burn_contracts.names import agent_secret_ref

PATH = "/burn-agents/"
POLICY_NAME = "burn-agent-access"
AWS_HOSTS = ["*.amazonaws.com"]


def user_name(agent_id: str) -> str:
    return f"burn-agent-{agent_id}"


def _register(secret_ref: str, owner: str, key: dict, version: int) -> None:
    fingerprints.register(secret_ref, owner, "aws_key", version, [
        ("access_key_id", key["access_key_id"], True, True),
        ("secret_access_key", key["secret_access_key"], True, True),
    ])


def _new_key(user: str) -> dict:
    created = aws.iam().create_access_key(UserName=user)["AccessKey"]
    return {"access_key_id": created["AccessKeyId"],
            "secret_access_key": created["SecretAccessKey"], "iam_user": user}


def _delete_keys(user: str, only_inactive: bool = False) -> None:
    for key in aws.iam().list_access_keys(UserName=user)["AccessKeyMetadata"]:
        if not only_inactive or key["Status"] == "Inactive":
            aws.iam().delete_access_key(UserName=user, AccessKeyId=key["AccessKeyId"])


def issue(agent_id: str) -> dict:
    secret_ref = agent_secret_ref(agent_id, "aws_key")
    user = user_name(agent_id)
    try:
        aws.iam().create_user(UserName=user, Path=PATH,
                              PermissionsBoundary=aws.setting("BOUNDARY_POLICY_ARN"))
    except aws.iam().exceptions.EntityAlreadyExistsException:
        _delete_keys(user)  # leftovers from an interrupted issue: no secret holds them
    aws.iam().put_user_policy(UserName=user, PolicyName=POLICY_NAME,
                              PolicyDocument=aws.setting("AGENT_POLICY_JSON"))
    key = _new_key(user)
    vault.put(secret_ref, json.dumps(key))
    _register(secret_ref, agent_id, key, 1)
    return registry.put(secret_ref, agent_id, "aws_key", 1, AWS_HOSTS, iam_user=user)


def rotate(row: dict) -> None:
    secret_ref, owner, old_version = row["secret_ref"], row["owner_agent_id"], int(row["version"])
    old = json.loads(vault.get(secret_ref))
    user = old["iam_user"]
    _delete_keys(user, only_inactive=True)  # IAM allows 2 keys per user; free a slot
    new = _new_key(user)
    vault.put(secret_ref, json.dumps(new))  # the old JSON is now AWSPREVIOUS
    # Keep the old key (Inactive, not deleted) so AWS visibly rejects it and
    # CloudTrail history for it stays easy to find.
    aws.iam().update_access_key(UserName=user, AccessKeyId=old["access_key_id"], Status="Inactive")
    fingerprints.mark_rotated(secret_ref, old_version)
    _register(secret_ref, owner, new, registry.bump_version(row))


def teardown(agent_id: str) -> None:
    user = user_name(agent_id)
    iam = aws.iam()
    try:
        _delete_keys(user)
        iam.delete_user_policy(UserName=user, PolicyName=POLICY_NAME)
        iam.delete_user(UserName=user)
    except iam.exceptions.NoSuchEntityException:
        pass  # already gone or partly created
