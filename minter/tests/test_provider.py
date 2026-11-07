"""Mock provider: current key -> 200, rotated key -> 401 plus a `rejected` event."""
import json

import boto3

import minter_app
import provider_app

AGENT = "agent-tester"


def mint():
    minter_app.handler({"method": "POST", "path": f"/agents/{AGENT}/credentials",
                        "body": {"types": ["api_key"]}})
    return boto3.client("secretsmanager").get_secret_value(SecretId=f"burn/{AGENT}/api_key")["SecretString"]


def whoami(token=None, scheme="Bearer", path="/v1/whoami"):
    headers = {"authorization": f"{scheme} {token}"} if token else {}
    resp = provider_app.handler({"rawPath": path, "headers": headers})
    return resp["statusCode"], json.loads(resp["body"])


def test_current_key_is_accepted():
    key = mint()
    assert whoami(key) == (200, {"agent_id": AGENT, "secret_ref": f"burn/{AGENT}/api_key"})


def test_rotated_key_is_rejected_and_recorded(tables):
    old = mint()
    minter_app.handler({"method": "POST", "path": f"/agents/{AGENT}/rotate", "body": {}})
    new = boto3.client("secretsmanager").get_secret_value(SecretId=f"burn/{AGENT}/api_key")["SecretString"]

    assert whoami(old) == (401, {"error": "key rotated"})
    assert whoami(new)[0] == 200

    events = tables["events"].scan()["Items"]
    assert len(events) == 1
    event = events[0]
    assert (event["source"], event["action"], event["agent_id"]) == ("provider", "rejected", AGENT)
    assert event["secret_ref"] == f"burn/{AGENT}/api_key" and event["stream"] == "all"
    assert old not in json.dumps(events, default=str)  # the event never carries the key


def test_unknown_missing_and_fragment_keys_are_401(tables):
    key = mint()
    assert whoami("mpk_live_" + "x" * 32)[0] == 401
    assert whoami()[0] == 401
    assert whoami(key, scheme="Basic")[0] == 401
    assert whoami(key[9:20])[0] == 401  # a fragment of a real key is not a key
    assert tables["events"].scan()["Items"] == []  # unknown keys are not attributed, so no event


def test_other_paths_are_404():
    assert whoami(mint(), path="/v1/other")[0] == 404
