"""End-to-end tests of the minter handler against moto."""
import json
import re

import boto3
import pytest

import aws
import minter_app
from burn_contracts import fingerprint as fp

AGENT = "agent-tester"


def call(method, path, body=None, query=None):
    resp = minter_app.handler({"method": method, "path": path, "body": body or {}, "query": query or {}})
    return resp["statusCode"], resp["body"]


def issue(agent=AGENT, types=("api_key", "aws_key")):
    status, body = call("POST", f"/agents/{agent}/credentials", {"types": list(types)})
    assert status == 200, body
    return body["credentials"]


def secret(secrets_client, ref, stage="AWSCURRENT"):
    return secrets_client.get_secret_value(SecretId=ref, VersionStage=stage)["SecretString"]


def key_bytes(secrets_client):
    return fp.key_from_hex(secret(secrets_client, "burn-system/fingerprint-key"))


def test_issue_api_key_format_registry_and_fingerprints(secrets_client, tables):
    creds = issue(types=["api_key"])
    assert creds[0]["secret_ref"] == f"burn/{AGENT}/api_key"
    value = secret(secrets_client, creds[0]["secret_ref"])
    assert re.fullmatch(r"mpk_live_[A-Za-z0-9]{32}", value)

    row = tables["credentials"].get_item(Key={"secret_ref": creds[0]["secret_ref"]})["Item"]
    assert (row["owner_agent_id"], row["secret_type"], row["version"], row["status"]) == (
        AGENT, "api_key", 1, "active")
    assert row["allowed_hosts"] == ["provider.example"] and row["created_at"]

    exact = tables["fingerprints"].get_item(
        Key={"fingerprint": fp.fingerprint(value, key_bytes(secrets_client))})["Item"]
    assert exact["status"] == "active" and exact["kind"] == "exact" and exact["secret_ref"] == row["secret_ref"]
    body = value[len("mpk_live_"):]
    sh = fp.shingle_fingerprint(body[3:11].lower(), key_bytes(secrets_client))
    assert tables["fingerprints"].get_item(Key={"fingerprint": sh})["Item"]["kind"] == "shingle"


def test_issue_is_idempotent(secrets_client):
    first = issue()
    value = secret(secrets_client, first[0]["secret_ref"])
    assert issue() == first
    assert secret(secrets_client, first[0]["secret_ref"]) == value


def test_issue_aws_key_is_boundary_limited_iam_user(secrets_client, monkeypatch):
    iam = aws.iam()
    create_user = iam.create_user
    seen = []  # moto does not echo the boundary back, so record what the minter asks for
    monkeypatch.setattr(iam, "create_user", lambda **kw: seen.append(kw) or create_user(**kw))
    issue(types=["aws_key"])
    assert seen[0]["PermissionsBoundary"].endswith("policy/burn-agent-boundary")
    user = iam.get_user(UserName=f"burn-agent-{AGENT}")["User"]
    assert user["Path"] == "/burn-agents/"
    policy = iam.get_user_policy(UserName=user["UserName"], PolicyName="burn-agent-access")["PolicyDocument"]
    actions = [a for s in policy["Statement"] for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])]
    assert sorted(actions) == ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "s3:ListAllMyBuckets"]
    stored = json.loads(secret(secrets_client, f"burn/{AGENT}/aws_key"))
    assert stored["iam_user"] == user["UserName"]
    keys = iam.list_access_keys(UserName=user["UserName"])["AccessKeyMetadata"]
    assert [k["AccessKeyId"] for k in keys] == [stored["access_key_id"]]


def test_rotate_api_key_keeps_previous_and_marks_fingerprints(secrets_client, tables):
    issue(types=["api_key"])
    ref = f"burn/{AGENT}/api_key"
    old = secret(secrets_client, ref)
    status, body = call("POST", f"/agents/{AGENT}/rotate", {"exposed_refs": []})
    assert (status, body) == (200, {"rotated": [ref], "flagged": [], "flag_levels": {}})

    new = secret(secrets_client, ref)
    assert new != old and secret(secrets_client, ref, "AWSPREVIOUS") == old
    key = key_bytes(secrets_client)
    old_row = tables["fingerprints"].get_item(Key={"fingerprint": fp.fingerprint(old, key)})["Item"]
    new_row = tables["fingerprints"].get_item(Key={"fingerprint": fp.fingerprint(new, key)})["Item"]
    assert (old_row["status"], old_row["version"]) == ("rotated", 1)
    assert (new_row["status"], new_row["version"]) == ("active", 2)
    assert tables["credentials"].get_item(Key={"secret_ref": ref})["Item"]["version"] == 2


def test_rotate_aws_key_deactivates_old_and_deletes_older_inactive(secrets_client, tables):
    issue(types=["aws_key"])
    ref, iam = f"burn/{AGENT}/aws_key", boto3.client("iam")
    first = json.loads(secret(secrets_client, ref))

    call("POST", f"/agents/{AGENT}/rotate", {})
    second = json.loads(secret(secrets_client, ref))
    status = {k["AccessKeyId"]: k["Status"]
              for k in iam.list_access_keys(UserName=first["iam_user"])["AccessKeyMetadata"]}
    assert status == {first["access_key_id"]: "Inactive", second["access_key_id"]: "Active"}
    assert json.loads(secret(secrets_client, ref, "AWSPREVIOUS")) == first

    call("POST", f"/agents/{AGENT}/rotate", {})
    third = json.loads(secret(secrets_client, ref))
    ids = {k["AccessKeyId"] for k in iam.list_access_keys(UserName=first["iam_user"])["AccessKeyMetadata"]}
    assert ids == {second["access_key_id"], third["access_key_id"]}  # the first one was cleaned up

    key = key_bytes(secrets_client)
    assert tables["fingerprints"].get_item(
        Key={"fingerprint": fp.fingerprint(first["access_key_id"], key)})["Item"]["status"] == "rotated"
    assert tables["fingerprints"].get_item(
        Key={"fingerprint": fp.fingerprint(third["secret_access_key"], key)})["Item"]["status"] == "active"


def test_rotate_only_touches_this_agents_credentials(secrets_client):
    issue("agent-a", ["api_key"])
    issue("agent-b", ["api_key"])
    before = secret(secrets_client, "burn/agent-b/api_key")
    call("POST", "/agents/agent-a/rotate", {})
    assert secret(secrets_client, "burn/agent-b/api_key") == before


def test_shared_credential_issue_flag_and_not_rotated(secrets_client, tables):
    status, body = call("POST", "/shared/credentials", {
        "name": "crm_db_url", "type": "other", "template": "postgres://svc:{random}@db.example/crm"})
    assert (status, body["secret_ref"], body["version"]) == (200, "burn/shared/crm_db_url", 1)
    value = secret(secrets_client, "burn/shared/crm_db_url")
    random_part = re.fullmatch(r"postgres://svc:([A-Za-z0-9]{24})@db\.example/crm", value).group(1)
    key = key_bytes(secrets_client)
    for text in (value, random_part):
        assert tables["fingerprints"].get_item(Key={"fingerprint": fp.fingerprint(text, key)})["Item"]

    assert call("POST", "/shared/credentials", {
        "name": "crm_db_url", "type": "other", "template": "postgres://svc:{random}@db.example/crm"})[1] == body
    assert secret(secrets_client, "burn/shared/crm_db_url") == value  # idempotent

    issue("agent-a", ["api_key"])
    _, result = call("POST", "/agents/agent-a/rotate", {
        "exposed_refs": ["burn/shared/crm_db_url", "burn/agent-a/api_key", "burn/agent-b/api_key", "junk"]})
    # own credential is rotated, shared one flagged; unregistered/garbage refs are ignored
    assert result == {"rotated": ["burn/agent-a/api_key"], "flagged": ["burn/shared/crm_db_url"],
                      "flag_levels": {"burn/shared/crm_db_url": "seen"}}
    row = tables["credentials"].get_item(Key={"secret_ref": "burn/shared/crm_db_url"})["Item"]
    assert row["status"] == "flagged" and row["flagged_by"] == "agent-a"
    assert secret(secrets_client, "burn/shared/crm_db_url") == value


def test_shared_template_needs_random_placeholder():
    status, body = call("POST", "/shared/credentials", {"name": "x", "type": "other", "template": "fixed"})
    assert status == 400 and "random" in body["error"]


def test_delete_removes_everything(secrets_client, tables):
    issue()
    status, body = call("DELETE", f"/agents/{AGENT}/credentials")
    assert status == 200 and sorted(body["deleted"]) == [f"burn/{AGENT}/api_key", f"burn/{AGENT}/aws_key"]
    assert secrets_client.list_secrets(Filters=[{"Key": "name", "Values": ["burn/"]}])["SecretList"] == []
    assert tables["credentials"].scan()["Items"] == []
    assert tables["fingerprints"].scan()["Items"] == []
    with pytest.raises(boto3.client("iam").exceptions.NoSuchEntityException):
        boto3.client("iam").get_user(UserName=f"burn-agent-{AGENT}")
    assert call("DELETE", f"/agents/{AGENT}/credentials")[1] == {"deleted": []}  # safe to repeat


def test_delete_api_key_only_agent_never_touches_iam(monkeypatch):
    issue(types=["api_key"])

    def forbidden(*args, **kwargs):  # real IAM answers AccessDenied for a user that never existed
        raise AssertionError("IAM must not be called for an agent without an aws_key")

    for name in ("list_access_keys", "delete_user_policy", "delete_user"):
        monkeypatch.setattr(aws.iam(), name, forbidden)
    assert call("DELETE", f"/agents/{AGENT}/credentials") == (200, {"deleted": [f"burn/{AGENT}/api_key"]})


def test_delete_shared():
    call("POST", "/shared/credentials", {"name": "tok", "type": "other", "template": "t-{random}"})
    assert call("DELETE", "/shared/credentials")[1] == {"deleted": ["burn/shared/tok"]}


def test_bad_requests():
    assert call("POST", "/agents/Not_An_Agent/credentials", {"types": ["api_key"]})[0] == 400
    assert call("POST", f"/agents/{AGENT}/credentials", {"types": ["root_key"]})[0] == 400
    assert call("GET", "/nope")[0] == 404


def test_recreating_a_just_deleted_secret_retries(monkeypatch, secrets_client):
    import vault
    monkeypatch.setattr(vault.time, "sleep", lambda s: None)
    client, real = aws.secrets(), aws.secrets().create_secret
    calls = []

    def flaky(**kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise client.exceptions.InvalidRequestException(
                {"Error": {"Code": "InvalidRequestException", "Message": "already scheduled for deletion"}},
                "CreateSecret")
        return real(**kwargs)

    monkeypatch.setattr(client, "create_secret", flaky)
    vault.put("burn/shared/tok", "v")
    assert len(calls) == 3 and secret(secrets_client, "burn/shared/tok") == "v"


def test_reissue_right_after_delete_and_rotation_keeps_previous(secrets_client):
    for _ in range(2):  # delete then immediately re-mint, twice in a row
        issue("agent-a", ["api_key"])
        shared_body = {"name": "tok", "type": "other", "template": "t-{random}"}
        assert call("POST", "/shared/credentials", shared_body)[0] == 200
        call("DELETE", "/agents/agent-a/credentials")
        call("DELETE", "/shared/credentials")
    # nothing is left in a recovery window: the names are gone for good
    with pytest.raises(secrets_client.exceptions.ResourceNotFoundException):
        secrets_client.describe_secret(SecretId="burn/agent-a/api_key")

    issue("agent-a", ["api_key"])  # rotation still leaves the old value at AWSPREVIOUS
    old = secret(secrets_client, "burn/agent-a/api_key")
    call("POST", "/agents/agent-a/rotate", {})
    assert secret(secrets_client, "burn/agent-a/api_key", "AWSPREVIOUS") == old


def test_put_restores_a_secret_that_is_in_a_recovery_window(secrets_client):
    import vault
    secrets_client.create_secret(Name="burn/shared/old", SecretString="v1")
    secrets_client.delete_secret(SecretId="burn/shared/old", RecoveryWindowInDays=7)
    assert vault.exists("burn/shared/old") is False and vault.get("burn/shared/old") is None
    vault.put("burn/shared/old", "v2")
    assert secret(secrets_client, "burn/shared/old") == "v2"
