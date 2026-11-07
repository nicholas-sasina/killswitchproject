"""Return the demo to a clean slate.

Clears ONLY the five demo tables and asks the minter to delete the credentials
of every agent in the loaded scenario plus the shared ones. Nothing else in the
account is touched (no other tables, no system secrets).
"""
import boto3

from common import REGION, invoke_minter, names

DEMO_TABLES = [names.EVENTS_TABLE, names.EXPOSURES_TABLE, names.SESSIONS_TABLE,
               names.MESSAGES_TABLE, names.SCENARIO_TABLE]


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


if __name__ == "__main__":
    reset()
