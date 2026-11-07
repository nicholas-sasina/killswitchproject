"""Return the demo to a clean slate.

Clears ONLY the five demo tables, asks the minter to delete the credentials
of every agent in the loaded scenario plus the shared ones, and deletes the
Cognito badges (app clients) of the sessions it removed. Nothing else in the
account is touched (no other tables, no system secrets, no other Cognito clients).

    python scripts/reset.py                 full reset
    python scripts/reset.py --orphans-only  only delete badges whose session row is gone
"""
import sys

import boto3

from common import REGION, invoke_minter, names

DEMO_TABLES = [names.EVENTS_TABLE, names.EXPOSURES_TABLE, names.SESSIONS_TABLE,
               names.MESSAGES_TABLE, names.SCENARIO_TABLE]


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


def reset(extra_agent_ids: list[str] = ()) -> None:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    # Delete credentials first: the agent list comes from the scenario table we clear next.
    for agent_id in sorted(set(loaded_agent_ids(dynamodb)) | set(extra_agent_ids)):
        invoke_minter("DELETE", f"/agents/{agent_id}/credentials")
        print(f"credentials of {agent_id} deleted")
    invoke_minter("DELETE", "/shared/credentials")
    for name in DEMO_TABLES:
        print(f"{name}: {clear_table(dynamodb, name)} items cleared")
    # Sessions are gone, so every Burn badge client is now orphaned.
    _remove_badges_live(keep_client_ids=set())


if __name__ == "__main__":
    if "--orphans-only" in sys.argv:
        _remove_badges_live(remaining_client_ids(boto3.resource("dynamodb", region_name=REGION)))
    else:
        reset()
