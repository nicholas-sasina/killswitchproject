"""Return the demo to a clean slate.

Clears ONLY the demo tables (and the join tokens, which belong to a run), asks
the minter to delete the credentials and canaries of every agent in the loaded
scenario plus the shared credentials, and deletes the Cognito badges (app
clients) of the sessions it removed. Roles are configuration and are kept.

Swarm instances are not in the scenario, so they are found in burn-sessions
BEFORE it is cleared: each one's credentials (IAM user and keys), canaries and
badge secret are deleted, whatever its status. Nothing else in the account is
touched (no other tables, no system secrets, no other Cognito clients). It is
safe to run again after a partial failure: nothing is cleared until every
instance has been cleaned.

A full reset is unchanged. --orphans-only additionally sweeps badge secrets
nothing references any more: clean_instances() finds one by looking its agent
up in burn-sessions, so a secret whose row was already cleared is invisible to
that path, and Secrets Manager bills it every month until something deletes it.

    python scripts/reset.py                 full reset
    python scripts/reset.py --orphans-only  only delete badge clients and badge
                                            secrets nothing references any more
"""
import sys

import boto3

from common import REGION, invoke_minter, names

DEMO_TABLES = [names.EVENTS_TABLE, names.EXPOSURES_TABLE, names.SESSIONS_TABLE,
               names.MESSAGES_TABLE, names.SCENARIO_TABLE, names.ENROLL_TOKENS_TABLE]


# Badge clients are named burn-<agent_id>-<session_id> (gateway/badges.py), e.g.
# burn-agent-reader-sess_ab12. Only names of that shape are ever deleted, so clients
# created by anything else are safe. Load-test clients (agent ids starting
# agent-eval-) share the shape but belong to the eval harness: never touched.
BADGE_PREFIX, BADGE_MARK, PROTECTED_PREFIX = "burn-agent-", "-sess_", "burn-agent-eval-"


def is_burn_badge(client_name: str) -> bool:
    return (client_name.startswith(BADGE_PREFIX) and BADGE_MARK in client_name
            and not client_name.startswith(PROTECTED_PREFIX))


def remove_badges(cognito, pool_id: str, keep_client_ids: set[str]) -> list[str]:
    """Delete Burn badge clients except those in keep_client_ids. Returns the deleted names."""
    deleted, token = [], None
    while True:
        kwargs = {"UserPoolId": pool_id, "MaxResults": 60, **({"NextToken": token} if token else {})}
        page = cognito.list_user_pool_clients(**kwargs)
        for client in page["UserPoolClients"]:
            if is_burn_badge(client["ClientName"]) and client["ClientId"] not in keep_client_ids:
                cognito.delete_user_pool_client(UserPoolId=pool_id, ClientId=client["ClientId"])
                deleted.append(client["ClientName"])
        token = page.get("NextToken")
        if not token:
            return deleted


def _remove_badges_live(keep_client_ids: set[str]) -> None:
    pool_id = boto3.client("ssm", region_name=REGION).get_parameter(
        Name="/burn/identity/user-pool-id")["Parameter"]["Value"]
    deleted = remove_badges(boto3.client("cognito-idp", region_name=REGION), pool_id, keep_client_ids)
    print(f"{len(deleted)} badge client(s) deleted: {', '.join(deleted) or '-'}")


def remaining_client_ids(dynamodb) -> set[str]:
    items = dynamodb.Table(names.SESSIONS_TABLE).scan(ProjectionExpression="client_id")["Items"]
    return {i["client_id"] for i in items if "client_id" in i}


def loaded_agent_ids(dynamodb) -> list[str]:
    item = dynamodb.Table(names.SCENARIO_TABLE).get_item(Key={"pk": "meta"}).get("Item")
    return item["data"]["agent_ids"] if item else []


def is_cleanable_agent(agent_id: str) -> bool:
    """Burn agent ids only; load-test agents (agent-eval-) belong to the eval harness."""
    return names.is_agent_id(agent_id) and not agent_id.startswith("agent-eval-")


def instance_agent_ids(dynamodb) -> list[str]:
    """Agent ids of every role instance in burn-sessions, any status (burned, exited, active).

    Read before the sessions table is cleared: afterwards the list of instances is gone.
    """
    table, ids, kwargs = dynamodb.Table(names.SESSIONS_TABLE), set(), {
        "FilterExpression": "attribute_exists(#r)", "ProjectionExpression": "agent_id",
        "ExpressionAttributeNames": {"#r": "role"}}
    while True:
        page = table.scan(**kwargs)
        ids.update(i["agent_id"] for i in page["Items"] if is_cleanable_agent(i["agent_id"]))
        if "LastEvaluatedKey" not in page:
            return sorted(ids)
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def delete_badge_secret(secrets, agent_id: str) -> None:
    try:
        secrets.delete_secret(SecretId=names.agent_secret_ref(agent_id, "badge"),
                              ForceDeleteWithoutRecovery=True)
    except secrets.exceptions.ResourceNotFoundException:
        pass  # already gone: a rerun after a partial failure


# Badge secrets are named burn/<agent_id>/badge. Derived from the contract
# helper rather than hardcoded, so a change there cannot silently orphan them.
# Named SECRET_* so they cannot be confused with BADGE_PREFIX above, which is
# the Cognito client name prefix.
_SAMPLE_AGENT = "agent-sample"
SECRET_PREFIX, SECRET_SUFFIX = names.agent_secret_ref(_SAMPLE_AGENT, "badge").split(_SAMPLE_AGENT)


def badge_secret_agent_ids(secrets) -> list[str]:
    """Agent ids that still have a badge secret, whatever their session status."""
    ids, kwargs = set(), {}
    while True:
        page = secrets.list_secrets(**kwargs)
        for secret in page.get("SecretList", []):
            name = secret["Name"]
            if name.startswith(SECRET_PREFIX) and name.endswith(SECRET_SUFFIX):
                agent_id = name[len(SECRET_PREFIX):-len(SECRET_SUFFIX)]
                if is_cleanable_agent(agent_id):
                    ids.add(agent_id)
        if not page.get("NextToken"):
            return sorted(ids)
        kwargs["NextToken"] = page["NextToken"]


def remove_orphaned_badge_secrets(secrets, keep_agent_ids: set[str]) -> list[str]:
    """Delete badge secrets no session row points at any more. Returns the agent ids.

    clean_instances() finds a badge secret by looking its agent up in
    burn-sessions, so a secret whose session row was already cleared is
    invisible to that path, and Secrets Manager bills it every month until
    something deletes it.

    Only run from --orphans-only, never from a full reset. A reset is followed
    by new sessions under the same scenario agent ids, and each of those adds a
    version to the agent's existing badge secret. Deleting the secrets in a
    reset would make the first start after it create the name again while
    Secrets Manager may still be freeing it. _store_badge() survives that, but
    only by retrying for up to 20 s of a session start's budget, and the secret
    is reused anyway.

    The Cognito side of this already exists: _remove_badges_live() sweeps
    orphaned badge clients the same way.
    """
    removed = [a for a in badge_secret_agent_ids(secrets) if a not in keep_agent_ids]
    for agent_id in removed:
        delete_badge_secret(secrets, agent_id)
    return removed


def clean_instances(agent_ids: list[str], minter, secrets) -> list[str]:
    """Delete what each instance left behind. Returns the agent ids that could not be cleaned."""
    failed = []
    for agent_id in agent_ids:
        try:
            minter("DELETE", f"/agents/{agent_id}/credentials")
            minter("DELETE", f"/agents/{agent_id}/canaries")
            delete_badge_secret(secrets, agent_id)
            print(f"instance {agent_id}: credentials, canaries and badge secret deleted")
        except Exception as exc:  # keep going; the rerun finishes the rest
            print(f"instance {agent_id}: cleanup failed ({type(exc).__name__})")
            failed.append(agent_id)
    return failed


def clear_table(dynamodb, name: str) -> int:
    table = dynamodb.Table(name)
    keys = [k["AttributeName"] for k in table.key_schema]
    count, kwargs = 0, {"ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(keys))),
                        "ExpressionAttributeNames": {f"#k{i}": k for i, k in enumerate(keys)}}
    while True:
        page = table.scan(**kwargs)
        with table.batch_writer() as batch:
            for item in page["Items"]:
                batch.delete_item(Key={k: item[k] for k in keys})
                count += 1
        if "LastEvaluatedKey" not in page:
            return count
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def reset(extra_agent_ids: list[str] = (), minter=invoke_minter, secrets=None, remove_badges_fn=None) -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    secrets = secrets or boto3.client("secretsmanager", region_name=REGION)
    # Everything below that reads the tables comes first: the lists come from the tables cleared next.
    failed = clean_instances(instance_agent_ids(dynamodb), minter, secrets)
    if failed:
        raise RuntimeError(f"could not clean {len(failed)} instance(s), nothing was cleared; "
                           f"run reset again: {', '.join(failed)}")
    for agent_id in sorted(set(loaded_agent_ids(dynamodb)) | set(extra_agent_ids)):
        minter("DELETE", f"/agents/{agent_id}/credentials")
        # Canaries are kept after a burn or exit so they stay attributable; a reset is when they go.
        minter("DELETE", f"/agents/{agent_id}/canaries")
        print(f"credentials and canaries of {agent_id} deleted")
    minter("DELETE", "/shared/credentials")
    for name in DEMO_TABLES:
        print(f"{name}: {clear_table(dynamodb, name)} items cleared")
    # Sessions are gone, so every Burn badge client is now orphaned.
    (remove_badges_fn or _remove_badges_live)(keep_client_ids=set())


if __name__ == "__main__":
    if "--orphans-only" in sys.argv:
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _remove_badges_live(remaining_client_ids(dynamodb))
        # Keep anything still referenced: a session row, or the loaded scenario.
        keep = set(instance_agent_ids(dynamodb)) | set(loaded_agent_ids(dynamodb))
        swept = remove_orphaned_badge_secrets(
            boto3.client("secretsmanager", region_name=REGION), keep)
        print(f"{len(swept)} orphaned badge secret(s) deleted: {', '.join(swept) or '-'}")
    else:
        reset()
