"""burn run: environment, retries, heartbeat, the local endpoints and the spawn path."""
import json
import os
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request

import pytest

import burn_cli.runner.run as runmod
from burn_cli.api import Api
from burn_cli.main import run as cli
from burn_cli.runner.badge import BadgeToken, TokenRefused
from burn_cli.runner.credentials import Badge, MissingSecrets, from_secrets
from burn_cli.runner.env import child_env, describe, proxy_url, scrub
from burn_cli.runner.run import EXIT_STOPPED, Instance
from fake_api import API_TOKEN
from fake_servers import gateway, issuer

JOIN = "bjt_" + "0123456789abcdef" + "." + "A" * 43
HOST_SECRETS = {
    "AWS_ACCESS_KEY_ID": "AKIAHOSTHOSTHOSTHOST", "AWS_SECRET_ACCESS_KEY": "host-secret", "AWS_SESSION_TOKEN": "host-session",
    "AWS_PROFILE": "prod", "AWS_SHARED_CREDENTIALS_FILE": "/home/me/.aws/credentials", "AWS_CONFIG_FILE": "/home/me/.aws/config",
    "AWS_WEB_IDENTITY_TOKEN_FILE": "/tok", "AWS_ROLE_ARN": "arn:aws:iam::1:role/x", "AWS_BEARER_TOKEN_BEDROCK": "b",
    "BURN_JOIN_TOKEN": JOIN, "BURN_DEMO_TOKEN": API_TOKEN, "GITHUB_TOKEN": "ghp_x", "OPENAI_API_KEY": "sk-x",
    "ANTHROPIC_API_KEY": "sk-ant-x", "SLACK_BOT_TOKEN": "xoxb-x", "MY_SERVICE_PASSWORD": "pw", "DATABASE_URL": "postgres://u:p@h/db",
}
DUMP_ENV = "import json, os, sys; json.dump(dict(os.environ), open(sys.argv[1], 'w'))"


@pytest.fixture
def stack(fake, monkeypatch, tmp_path):
    gw, badge = gateway(), issuer()
    fake.roles["worker"] = {"name": "worker", "tools": ["read_inbox"], "credentials": ["api_key", "aws_key"],
                            "may_spawn": ["worker"], "managed_by": "code"}
    fake.token = fake.handle("POST", "/roles/worker/tokens", {}, {"uses": 10})[1]["token"]
    monkeypatch.setenv("BURN_JOIN_TOKEN", fake.token)
    monkeypatch.setenv("BURN_GATEWAY_URL", gw.url + "/mcp")
    fake.badge_url = badge.url + "/token"
    fake.gw, fake.badge, fake.tmp = gw, badge, tmp_path
    yield fake
    gw.close()
    badge.close()


def host_env(stack, **extra):
    env = {"PATH": os.environ["PATH"], "HOME": "/home/me", "AWS_REGION": "us-east-1", **HOST_SECRETS, **extra}
    env.update({"BURN_API_URL": stack.url, "BURN_GATEWAY_URL": stack.gw.url + "/mcp",
                "PYTHONPATH": os.pathsep.join(sys.path)})
    return env


def instance(stack, command, **kw):
    kw.setdefault("heartbeat_every", 30.0)
    return Instance(Api(stack.url, API_TOKEN), "worker", command,
                    gateway_url=stack.gw.url + "/mcp", host_env=host_env(stack), enroll_token=stack.token,
                    sleep=lambda _: None, **kw)


def py(code, *args):
    return [sys.executable, "-c", textwrap.dedent(code), *args]


# -- environment -------------------------------------------------------------------------------
def test_child_gets_its_own_values_and_none_of_the_hosts(stack):
    out = stack.tmp / "env.json"
    inst = instance(stack, py(DUMP_ENV, str(out)), proxy_url="http://proxy:8080")
    assert inst.run() == 0
    env = json.loads(out.read_text())
    for name, value in HOST_SECRETS.items():
        assert name not in env or env[name] != value, name
    assert not [k for k in env if k.startswith("AWS_") and k not in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION")]
    assert env["AWS_ACCESS_KEY_ID"].startswith("AKIA") and env["AWS_ACCESS_KEY_ID"] != HOST_SECRETS["AWS_ACCESS_KEY_ID"]
    assert env["AWS_SECRET_ACCESS_KEY"] != "host-secret" and env["BURN_API_KEY"].startswith("mpk_live_")
    session = stack.calls("POST", "/sessions")[0]
    agent_id = next(iter(stack.instances.values()))["agent_id"]
    assert env["BURN_AGENT_ID"] == agent_id and env["BURN_ROLE"] == "worker"
    assert env["BURN_SESSION_ID"] in stack.instances
    assert env["BURN_MCP_URL"].startswith("http://127.0.0.1:") and env["BURN_MCP_URL"].endswith("/mcp")
    assert env["BURN_SPAWN_URL"].startswith("http://127.0.0.1:")
    identity = f"{agent_id}:{env['BURN_SESSION_ID']}"
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == f"http://{identity}@proxy:8080"
    assert "127.0.0.1" in env["NO_PROXY"]
    assert API_TOKEN not in json.dumps(env) and JOIN not in json.dumps(env) and stack.token not in json.dumps(env)
    assert session[2]["enroll_token"] == stack.token


def test_no_proxy_means_no_proxy_variables(stack):
    out = stack.tmp / "env.json"
    instance(stack, py(DUMP_ENV, str(out))).run()
    assert not [k for k in json.loads(out.read_text()) if "proxy" in k.lower()]


def test_only_allow_listed_host_variables_reach_the_agent():
    kept = scrub({"PATH": "/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "TERM": "xterm", "AWS_REGION": "x",
                  "SSL_CERT_FILE": "/c.pem", "AWS_CA_BUNDLE": "/c.pem", "HOME": "/h", "USER": "me", "SHELL": "/bin/zsh",
                  "FOO_TOKEN": "t", "AWS_FUTURE_THING": "x", "SOME_NEW_THING": "x"})
    assert kept == {"PATH": "/bin", "LANG": "C", "LC_ALL": "C", "TZ": "UTC", "TERM": "xterm", "AWS_REGION": "x",
                    "SSL_CERT_FILE": "/c.pem", "AWS_CA_BUNDLE": "/c.pem"}


@pytest.mark.parametrize("name,value", [
    ("SECRET_KEY", "django-insecure"), ("PGPASSWORD", "pw"), ("MYSQL_PWD", "pw"), ("OPENAI_KEY", "sk"),
    ("GITHUB_PAT", "ghp"), ("SENTRY_DSN", "https://k@o.ingest.sentry.io/1"), ("MONGODB_URI", "mongodb://u:p@h/db"),
    ("CELERY_BROKER_URL", "amqp://u:p@h//"), ("DB_CONNECTION_STRING", "Server=h;Password=p"),
    ("HTTPS_PROXY", "http://user:pass@corp-proxy:3128"), ("ALL_PROXY", "socks5://u:p@h:1080"),
    ("http_proxy", "http://user:pass@corp-proxy:3128"), ("NO_PROXY", "internal.example"), ("PYTHONPATH", "/opt/private-libs")])
def test_credential_looking_variables_the_old_deny_list_missed_do_not_reach_the_agent(stack, name, value):
    out = stack.tmp / "env.json"
    inst = instance(stack, py(DUMP_ENV, str(out)))
    inst.host_env[name] = value
    assert inst.run() == 0
    env = json.loads(out.read_text())
    assert name not in env and value not in json.dumps(env)


def test_proxy_url_keeps_existing_userinfo_and_adds_the_instance_identity():
    scheme, host = "http", "proxy:8080"
    with_user = f"{scheme}://svc:pw@{host}"
    assert proxy_url(with_user, "agent-a", "s") == with_user
    assert proxy_url(f"{scheme}://{host}", "agent-a-1", "sess_1") == f"{scheme}://agent-a-1:sess_1@{host}"


def test_env_command_hides_values_and_never_prints_removed_ones(capsys, monkeypatch):
    for k, v in HOST_SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BURN_PROXY_URL", "http://proxy:8080")
    assert cli(["env"]) == 0
    out = capsys.readouterr().out
    assert "AWS_SECRET_ACCESS_KEY" in out and "MY_SERVICE_PASSWORD" in out and "host-secret" not in out and JOIN not in out and API_TOKEN not in out
    assert "<redacted>" in out and "convenience, not containment" in out
    assert cli(["env", "--show"]) == 0
    shown = capsys.readouterr().out
    assert os.environ["PATH"] in shown and "host-secret" not in shown and JOIN not in shown


def test_describe_lists_removed_names_only():
    lines = "\n".join(describe({"GITHUB_TOKEN": "ghp_secret", "PATH": "/bin"}, show=True))
    assert "GITHUB_TOKEN" in lines and "ghp_secret" not in lines


# -- retry ---------------------------------------------------------------------------------------
def test_session_start_retries_429_then_succeeds_with_backoff(stack):
    stack.fail[("POST", "/sessions")] = [429, 503]
    waits = []
    inst = instance(stack, py("pass"))
    inst._sleep = waits.append
    assert inst.run() == 0
    assert len(stack.calls("POST", "/sessions")) == 3 and len(waits) == 2
    assert waits[0] <= 0.5 and waits[1] <= 1.0  # jitter keeps each wait under its doubling ceiling


def test_session_start_does_not_retry_a_rejected_token(stack):
    stack.tokens.clear()
    with pytest.raises(Exception, match="HTTP 403"):
        instance(stack, py("pass")).run()
    assert len(stack.calls("POST", "/sessions")) == 1


# -- lifecycle ---------------------------------------------------------------------------------
def test_clean_exit_is_reported_and_the_exit_code_passes_through(stack):
    assert instance(stack, py("import sys; sys.exit(7)")).run() == 7
    assert len(stack.calls("POST", "/instances/" + next(iter(stack.instances)) + "/exit")) == 1
    assert next(iter(stack.instances.values()))["status"] == "exited"


def test_heartbeat_409_stops_the_agent(stack):
    stack.heartbeat_status = 409
    marker = stack.tmp / "still-running"
    inst = instance(stack, py(f"import time, pathlib; time.sleep(30); pathlib.Path({str(marker)!r}).write_text('x')"),
                    heartbeat_every=0.1, stop_grace=2)
    started = time.monotonic()
    assert inst.run() == EXIT_STOPPED
    assert time.monotonic() - started < 10 and not marker.exists()
    assert not stack.calls("POST", "/instances/" + next(iter(stack.instances)) + "/exit")  # burned: nothing to report


def test_a_stubborn_agent_is_killed_after_the_grace_period(stack):
    stack.heartbeat_status = 409
    inst = instance(stack, py("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"),
                    heartbeat_every=0.1, stop_grace=0.5)
    started = time.monotonic()
    assert inst.run() == EXIT_STOPPED and time.monotonic() - started < 10


def test_heartbeat_survives_transient_errors(stack):
    original, failed = stack.handle, []

    def flaky(method, path, query, body):
        if path.endswith("/heartbeat") and not failed:
            failed.append(path)
            return 503, {"error": "busy"}
        return original(method, path, query, body)

    stack.handle = flaky
    assert instance(stack, py("import time; time.sleep(1)"), heartbeat_every=0.1).run() == 0
    assert len(stack.calls("POST", failed[0])) >= 2  # the beat after the 503 still went out


def test_signal_handlers_are_installed_and_forward(stack):
    """Run burn as a real process and send it SIGTERM."""
    out = stack.tmp / "got-term"
    child = f"""
import signal, sys, time, pathlib
signal.signal(signal.SIGTERM, lambda *a: (pathlib.Path({str(out)!r}).write_text('term'), sys.exit(0)))
pathlib.Path({str(out) + '.ready'!r}).write_text('1')
time.sleep(30)"""
    env = {**host_env(stack), "BURN_DEMO_TOKEN": API_TOKEN, "BURN_JOIN_TOKEN": stack.token}
    proc = subprocess.Popen([sys.executable, "-m", "burn_cli", "run", "--role", "worker", "--", sys.executable, "-c", child], env=env)
    for _ in range(200):
        if (stack.tmp / "got-term.ready").exists():
            break
        time.sleep(0.05)
    proc.terminate()
    assert proc.wait(10) == 0 and out.read_text() == "term"
    assert next(iter(stack.instances.values()))["status"] == "exited"


def test_command_that_cannot_start(stack):
    assert instance(stack, ["/no/such/binary"]).run() == 127
    assert next(iter(stack.instances.values()))["status"] == "exited"


# -- local MCP endpoint --------------------------------------------------------------------------
def test_mcp_endpoint_adds_the_badge_token_and_hides_the_secret(stack):
    out = stack.tmp / "result.json"
    code = """
        import json, os, sys, urllib.request
        req = urllib.request.Request(os.environ["BURN_MCP_URL"] + "/tools", data=b'{"x":1}', method="POST",
                                     headers={"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"], "X-Custom": "kept"})
        body = json.loads(urllib.request.urlopen(req).read())
        json.dump({"body": body, "env": dict(os.environ)}, open(sys.argv[1], "w"))"""
    assert instance(stack, py(code, str(out))).run() == 0
    result = json.loads(out.read_text())
    (seen,) = stack.gw.seen
    assert seen["path"] == "/mcp/tools" and seen["body"] == b'{"x":1}' and seen["headers"]["X-Custom"] == "kept"
    assert seen["headers"]["Authorization"] == "Bearer token-1"  # the local token is swapped for the badge token
    assert result["env"]["BURN_LOCAL_TOKEN"] not in json.dumps(seen["headers"])
    client_id, client_secret = stack.badge.state["clients"][0]
    assert client_id.startswith("agent-worker-")
    blob = json.dumps(result)
    assert client_secret not in blob and "token-1" not in blob


def test_mcp_endpoint_passes_gateway_refusals_through(stack):
    out = stack.tmp / "status"
    stack.gw.close()
    stack.gw = type(stack.gw)(lambda h, b: (403, {"error": "BURN: badge revoked"}))
    code = """
        import os, sys, urllib.request, urllib.error
        try:
            urllib.request.urlopen(urllib.request.Request(os.environ["BURN_MCP_URL"], data=b"{}", method="POST", headers={"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"]}))
        except urllib.error.HTTPError as e:
            open(sys.argv[1], "w").write(f"{e.code} {e.read().decode()}")"""
    instance(stack, py(code, str(out))).run()
    assert out.read_text().startswith("403") and "BURN: badge revoked" in out.read_text()


def test_mcp_endpoint_reports_a_refused_badge(stack):
    stack.badge.close()
    stack.badge = issuer(status=401)
    stack.badge_url = stack.badge.url + "/token"
    out = stack.tmp / "status"
    code = """
        import os, sys, urllib.request, urllib.error
        try:
            urllib.request.urlopen(urllib.request.Request(os.environ["BURN_MCP_URL"], data=b"{}", method="POST", headers={"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"]}))
        except urllib.error.HTTPError as e:
            open(sys.argv[1], "w").write(f"{e.code} {e.read().decode()}")"""
    inst = Instance(Api(stack.url, API_TOKEN), "worker", py(code, str(out)),
                    gateway_url=stack.gw.url + "/mcp", host_env=host_env(stack), enroll_token=stack.token, sleep=lambda _: None)
    inst.run()
    assert out.read_text().startswith("401") and "badge" in out.read_text() and not stack.gw.seen


def test_mcp_activity_feeds_the_heartbeat(stack):
    code = """
        import os, time, urllib.request
        for _ in range(3):
            urllib.request.urlopen(urllib.request.Request(os.environ["BURN_MCP_URL"], data=b"{}", method="POST", headers={"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"]})).read()
            time.sleep(0.4)"""
    inst = instance(stack, py(code), heartbeat_every=60.0, min_heartbeat_gap=0.1)
    assert inst.run() == 0
    # heartbeat_every is a minute, so every beat here was caused by an MCP call
    assert len(stack.calls("POST", f"/instances/{next(iter(stack.instances))}/heartbeat")) >= 2


def test_badge_token_refreshes_before_it_expires():
    now, minted = [0.0], []
    badge = BadgeToken(Badge("agent-a", "s", "http://issuer"), clock=lambda: now[0],
                       post=lambda url, data, user, pw: minted.append(1) or {"access_token": f"t{len(minted)}", "expires_in": 300})
    assert badge.get() == "t1" and badge.get() == "t1"
    now[0] = 239
    assert badge.get() == "t1"
    now[0] = 241  # within the 60 s margin of the 300 s lifetime
    assert badge.get() == "t2"


def test_badge_refusal_is_reported(stack):
    iss = issuer(status=403)
    with pytest.raises(TokenRefused):
        BadgeToken(Badge("agent-a", "s", iss.url)).get()
    iss.close()


# -- spawn ---------------------------------------------------------------------------------------
def test_spawn_starts_a_child_with_its_own_credentials_and_endpoints(stack):
    child_out = stack.tmp / "child.json"
    parent_out = stack.tmp / "parent.json"
    child_cmd = [sys.executable, "-c", DUMP_ENV, str(child_out)]
    parent = f"""
        import json, os, sys, time, pathlib
        import burn
        got = burn.spawn("worker", {child_cmd!r})
        for _ in range(200):
            if pathlib.Path({str(child_out)!r}).exists(): break
            time.sleep(0.05)
        json.dump({{"spawned": got, "env": dict(os.environ)}}, open({str(parent_out)!r}, "w"))"""
    assert instance(stack, py(parent)).run() == 0
    parent_env = json.loads(parent_out.read_text())
    spawned, penv = parent_env["spawned"], parent_env["env"]
    time.sleep(0.5)
    sessions = stack.calls("POST", "/sessions")
    assert sessions[0][2]["enroll_token"] == stack.token
    assert sessions[1][2] == {"role": "worker", "parent_session": penv["BURN_SESSION_ID"]}
    cenv = json.loads(child_out.read_text())
    assert cenv["BURN_SESSION_ID"] == spawned["session_id"] and cenv["BURN_AGENT_ID"] == spawned["agent_id"]
    assert spawned["agent_id"] != penv["BURN_AGENT_ID"]
    assert cenv["AWS_ACCESS_KEY_ID"] != penv["AWS_ACCESS_KEY_ID"] and cenv["BURN_API_KEY"] != penv["BURN_API_KEY"]
    assert cenv["BURN_MCP_URL"] != penv["BURN_MCP_URL"] and cenv["BURN_SPAWN_URL"] != penv["BURN_SPAWN_URL"]
    assert stack.instances[spawned["session_id"]]["parent_session"] == penv["BURN_SESSION_ID"]
    assert API_TOKEN not in json.dumps(cenv) and stack.token not in json.dumps(cenv)


def test_spawn_of_a_role_the_parent_may_not_start_is_refused(stack):
    stack.roles["admin"] = {"name": "admin", "tools": [], "managed_by": "code"}
    out = stack.tmp / "out"
    code = f"""
        import burn, pathlib
        try:
            burn.spawn("admin", ["true"])
        except burn.BurnError as e:
            pathlib.Path({str(out)!r}).write_text(str(e))"""
    instance(stack, py(code)).run()
    assert "HTTP 403" in out.read_text()
    assert len([i for i in stack.instances.values() if i["role"] == "admin"]) == 0


def test_spawn_rejects_a_malformed_request(stack):
    out = stack.tmp / "out"
    code = f"""
        import os, urllib.request, urllib.error, pathlib
        try:
            urllib.request.urlopen(urllib.request.Request(os.environ["BURN_SPAWN_URL"], data=b'{{"role": "worker"}}', method="POST", headers={{"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"]}}))
        except urllib.error.HTTPError as e:
            pathlib.Path({str(out)!r}).write_text(str(e.code))"""
    instance(stack, py(code)).run()
    assert out.read_text() == "400" and len(stack.calls("POST", "/sessions")) == 1


# -- credentials ---------------------------------------------------------------------------------
def test_credentials_come_from_the_join_replys_secrets():
    creds = from_secrets({"badge": {"client_id": "cid", "client_secret": "csecret", "token_url": "https://issuer/token",
                                    "scope": "gw/tools"},
                          "api_key": "mpk_live_" + "a" * 32, "aws_key": {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "sak"}})
    assert creds.badge == Badge("cid", "csecret", "https://issuer/token", "gw/tools")
    assert (creds.api_key, creds.aws_access_key_id, creds.aws_secret_access_key) == ("mpk_live_" + "a" * 32, "AKIAEXAMPLE", "sak")
    assert "csecret" not in repr(creds) and "sak" not in repr(creds)
    assert from_secrets({"badge": {"client_id": "c", "client_secret": "s", "token_url": "u"}}).api_key is None


@pytest.mark.parametrize("reply", [None, {}, {"api_key": "k"}])
def test_a_reply_without_secrets_is_a_clear_error(reply):
    with pytest.raises(MissingSecrets, match="API too old: no secrets in the join reply"):
        from_secrets(reply)


def test_an_api_that_returns_no_secrets_fails_clearly_ends_the_session_and_never_asks_aws(stack, capsys, monkeypatch):
    stack.omit_secrets = True
    monkeypatch.setitem(sys.modules, "boto3", None)  # importing boto3 would now fail: there is no AWS fallback
    code = cli(["run", "--role", "worker", "--", "true"])
    assert code == 1 and "API too old: no secrets in the join reply" in capsys.readouterr().err
    (row,) = stack.instances.values()
    assert row["status"] == "exited"  # the session that was started is not left dangling


def test_the_runner_does_not_depend_on_aws():
    import importlib.util
    import pathlib
    root = pathlib.Path(runmod.__file__).parents[1]
    assert not [p for p in root.rglob("*.py") if "boto" in p.read_text() and p.name != "env.py"]
    assert importlib.util.find_spec("burn_cli.runner.credentials") is not None


# -- the CLI command -------------------------------------------------------------------------------
def test_burn_run_never_prints_the_join_or_api_token(stack, capfd, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    code = cli(["run", "--role", "worker", "--", sys.executable, "-c", "print('agent says hi')"])
    out = capfd.readouterr()
    assert code == 0 and "agent says hi" in out.out and "started" in out.err
    assert stack.token not in out.out + out.err and API_TOKEN not in out.out + out.err


def test_burn_run_needs_a_command_and_a_token(stack, capsys, monkeypatch):
    assert cli(["run", "--role", "worker"]) == 2
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    assert cli(["run", "--role", "worker", "--", "true"]) == 2
    assert "join token is required" in capsys.readouterr().err


def test_burn_run_needs_a_gateway(stack, capsys, monkeypatch):
    monkeypatch.delenv("BURN_GATEWAY_URL")
    assert cli(["run", "--role", "worker", "--", "true"], dotenv=stack.tmp / "none") == 2


# -- who the agent runs as -----------------------------------------------------------------------
REAL_POPEN = subprocess.Popen


class FakePopen:
    """Records how a process was started, then starts it for real without the user switch."""
    calls: list[tuple[list, dict]] = []

    def __new__(cls, argv, **kw):
        cls.calls.append((list(argv), dict(kw)))
        for k in ("user", "group", "extra_groups"):
            kw.pop(k, None)
        if "burn_cli" in argv:  # a child supervisor: do not really start one
            return REAL_POPEN(["true"])
        return REAL_POPEN(argv, **kw)


def test_root_starts_the_agent_as_an_unprivileged_user_but_not_child_supervisors(stack, monkeypatch):
    from burn_cli.runner import privs
    drop = privs.Drop("agent", 4242, 4343)
    monkeypatch.setattr(runmod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(runmod.os, "chown", lambda *a: None)
    FakePopen.calls.clear()
    inst = instance(stack, py("pass"), drop=drop, user_arg="agent")
    assert inst.run() == 0
    (_, kw), = FakePopen.calls
    assert kw["user"] == 4242 and kw["group"] == 4343
    assert kw["extra_groups"] == []  # no supplementary groups, root's included
    # a child supervisor is not the agent: it stays root, is told the user, and drops privileges for its own agent
    stack.roles["worker"]["may_spawn"] = ["worker"]
    parent = stack.add_instance("sess_parent", "worker")
    inst.session = {"session_id": "sess_parent", "agent_id": parent["agent_id"]}
    inst._stopping.clear()
    FakePopen.calls.clear()
    inst.spawn_child("worker", ["true"])
    argv, kw = FakePopen.calls[0]
    assert "user" not in kw and argv[argv.index("--user") + 1] == "agent"
    inst._stop_children()


@pytest.fixture
def claims(tmp_path, monkeypatch):
    from burn_cli.runner import privs
    monkeypatch.setattr(privs.os, "geteuid", lambda: 0)
    monkeypatch.setattr(privs, "_claim_dir", lambda: str(tmp_path))
    return tmp_path


def test_every_instance_gets_its_own_uid_from_the_reserved_range(claims):
    from burn_cli.runner import privs
    drops = [privs.resolve(None) for _ in range(200)]
    uids = {d.uid for d in drops}
    assert len(uids) == 200  # no two live instances share a uid, so none can read another's environ or HOME
    assert all(privs.UID_BASE <= d.uid < privs.UID_BASE + privs.UID_COUNT and d.gid == d.uid for d in drops)
    assert all(d.popen_args() == {"user": d.uid, "group": d.uid, "extra_groups": []} for d in drops)


def test_a_uid_is_never_handed_out_twice_even_after_its_instance_ended(claims):
    from burn_cli.runner import privs
    import random
    first = privs.allocate(random.Random(1))
    # same random stream: the first draw collides with the claim, which is kept so leftovers (files in /tmp,
    # a process that forked during the kill scan) can never be seen by a later instance
    assert privs.allocate(random.Random(1)).uid != first.uid
    assert not hasattr(privs, "release")


def test_a_shared_user_is_never_swept_with_kill_all(stack, monkeypatch):
    from burn_cli.runner import privs
    swept = []
    monkeypatch.setattr(privs, "kill_all", swept.append)
    monkeypatch.setattr(runmod.os, "chown", lambda *a: None)
    monkeypatch.setattr(runmod.subprocess, "Popen", FakePopen)
    assert instance(stack, py("pass"), drop=privs.Drop("svc", 999, 999)).run() == 0  # --user: no claim, shared
    assert swept == []
    assert instance(stack, py("pass"), drop=privs.Drop("burn-100001", 100001, 100001, "/run/x")).run() == 0
    assert swept == [100001]  # an instance's own uid is swept


def test_claim_directory_must_be_private_and_root_owned(monkeypatch, tmp_path):
    from burn_cli.runner import privs
    monkeypatch.setattr(privs.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(privs.os.path, "isdir", lambda p: False if p == "/run" else os.path.isdir(p))
    with pytest.raises(ValueError, match="owned by root"):  # not root here, so the directory is "someone else's"
        privs._claim_dir()


def test_named_user_is_used_as_given_and_never_root(claims, monkeypatch):
    from burn_cli.runner import privs
    users = {"svc": 999, "toor": 0}
    monkeypatch.setattr(privs.pwd, "getpwnam", lambda n: type("E", (), {"pw_uid": users[n], "pw_gid": users[n]})()
                        if n in users else (_ for _ in ()).throw(KeyError(n)))
    assert privs.resolve("svc").uid == 999
    with pytest.raises(ValueError, match="root"):
        privs.resolve("toor")
    with pytest.raises(ValueError, match="does not exist"):
        privs.resolve("ghost")


def test_kill_all_signals_only_processes_of_that_uid(monkeypatch):
    from burn_cli.runner import privs
    killed = []
    monkeypatch.setattr(privs.os, "listdir", lambda p: ["1", "20", "21", "self"])
    monkeypatch.setattr(privs.os, "stat", lambda p: type("S", (), {"st_uid": 100500 if p.endswith(("/20", "/21")) else 0})())
    monkeypatch.setattr(privs.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    privs.kill_all(100500)
    assert killed == [(20, privs.signal.SIGKILL), (21, privs.signal.SIGKILL)]


def test_not_root_runs_the_agent_as_the_same_user(monkeypatch):
    from burn_cli.runner import privs
    monkeypatch.setattr(privs.os, "geteuid", lambda: 1000)
    assert privs.resolve(None) is None
    with pytest.raises(ValueError, match="needs burn run to be root"):
        privs.resolve("agent")


def test_the_help_says_what_a_non_root_agent_can_read(capsys):
    with pytest.raises(SystemExit):
        cli(["run", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "not containment" in out and "can read your files and burn run's environment" in out


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to switch users")
def test_real_process_the_agent_cannot_read_the_supervisors_environment(stack, capfd):
    from burn_cli.runner import privs
    code = """
        import json, os
        try:
            open(f"/proc/{os.getppid()}/environ", "rb").read(); readable = True
        except OSError:
            readable = False
        print(json.dumps({"uid": os.getuid(), "gid": os.getgid(), "supervisor_environ_readable": readable}))"""
    assert instance(stack, py(code), drop=privs.resolve(None)).run() == 0
    result = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert result["uid"] != 0 and result["gid"] != 0 and result["supervisor_environ_readable"] is False


# -- a private HOME ----------------------------------------------------------------------------------
def test_agent_gets_a_fresh_private_home_removed_at_exit(stack):
    host_home = stack.tmp / "realhome"
    (host_home / ".aws").mkdir(parents=True)
    (host_home / ".aws" / "credentials").write_text("[default]\n")
    out = stack.tmp / "home.json"
    code = """
        import json, os, stat, sys
        home = os.environ["HOME"]
        open(os.path.join(home, "scratch"), "w").write("x")
        json.dump({"home": home, "mode": stat.S_IMODE(os.stat(home).st_mode), "aws": os.path.exists(os.path.expanduser("~/.aws")),
                   "xdg": os.environ["XDG_CONFIG_HOME"], "cache": os.environ["XDG_CACHE_HOME"]}, open(sys.argv[1], "w"))"""
    inst = instance(stack, py(code, str(out)))
    inst.host_env["HOME"] = str(host_home)
    inst.host_env["XDG_CONFIG_HOME"] = str(host_home / ".config")
    assert inst.run() == 0
    seen = json.loads(out.read_text())
    assert seen["home"] != str(host_home) and seen["mode"] == 0o700 and seen["aws"] is False
    assert seen["xdg"].startswith(seen["home"]) and seen["cache"].startswith(seen["home"])
    assert not os.path.exists(seen["home"])  # gone at exit, with what the agent wrote there
    assert (host_home / ".aws" / "credentials").exists()


def test_home_is_chowned_to_the_agent_user_and_removed_when_the_agent_is_stopped(stack, monkeypatch):
    from burn_cli.runner import privs
    chowned = []
    monkeypatch.setattr(runmod.os, "chown", lambda path, uid, gid: chowned.append((path, uid, gid)))
    monkeypatch.setattr(runmod.subprocess, "Popen", FakePopen)
    stack.heartbeat_status = 409
    inst = instance(stack, py("import time; time.sleep(30)"), drop=privs.Drop("agent", 4242, 4343),
                    heartbeat_every=0.1, stop_grace=1)
    assert inst.run() == EXIT_STOPPED
    (path, uid, gid), = chowned
    assert (uid, gid) == (4242, 4343) and "burn-home-" in path and not os.path.exists(path)


def test_child_env_without_a_home_has_none():
    creds = from_secrets({"badge": {"client_id": "c", "client_secret": "s", "token_url": "u"}})
    env = child_env({"HOME": "/h"}, session={"session_id": "s", "agent_id": "agent-a"}, credentials=creds,
                    mcp_url="u", spawn_url="v")
    assert "HOME" not in env  # the host's home is never passed on


# -- local endpoints need the per-run token ------------------------------------------------------------
def test_local_endpoints_refuse_callers_without_the_per_run_token(stack):
    out = stack.tmp / "codes.json"
    code = """
        import json, os, sys, urllib.request, urllib.error
        def status(url, headers):
            try:
                return urllib.request.urlopen(urllib.request.Request(url, data=b'{"role": "worker", "command": ["true"]}',
                                                                      method="POST", headers=headers)).status
            except urllib.error.HTTPError as e:
                return e.code
        good = {"Authorization": "Bearer " + os.environ["BURN_LOCAL_TOKEN"]}
        json.dump({
            "mcp_none": status(os.environ["BURN_MCP_URL"], {}),
            "mcp_wrong": status(os.environ["BURN_MCP_URL"], {"Authorization": "Bearer nope"}),
            "spawn_none": status(os.environ["BURN_SPAWN_URL"], {}),
            "spawn_wrong": status(os.environ["BURN_SPAWN_URL"], {"Authorization": "Bearer nope"}),
            "mcp_good": status(os.environ["BURN_MCP_URL"], good),
            "token": os.environ["BURN_LOCAL_TOKEN"]}, open(sys.argv[1], "w"))"""
    assert instance(stack, py(code, str(out)), heartbeat_every=60.0).run() == 0
    codes = json.loads(out.read_text())
    assert [codes[k] for k in ("mcp_none", "mcp_wrong", "spawn_none", "spawn_wrong")] == [401] * 4
    assert codes["mcp_good"] == 200
    assert len(stack.gw.seen) == 1  # only the authorized call reached the Gateway
    assert len(stack.calls("POST", "/sessions")) == 1  # no child was started by the refused spawn requests
    assert len(codes["token"]) >= 40


def test_each_run_gets_its_own_local_token(stack):
    tokens = []
    for _ in range(2):
        out = stack.tmp / f"t{len(tokens)}.json"
        instance(stack, py(DUMP_ENV, str(out))).run()
        tokens.append(json.loads(out.read_text())["BURN_LOCAL_TOKEN"])
    assert tokens[0] != tokens[1]


def test_token_stdin_reads_the_join_token_and_never_echoes_it(stack, capfd, monkeypatch):
    import io
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sys.path))
    token = stack.token
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    monkeypatch.setattr(sys, "stdin", io.StringIO(token + "\nmore\n"))
    assert cli(["run", "--role", "worker", "--token-stdin", "--", sys.executable, "-c", "print('ok')"]) == 0
    out = capfd.readouterr()
    assert token not in out.out + out.err and stack.calls("POST", "/sessions")[0][2]["enroll_token"] == token


def test_token_stdin_conflicts_with_token_and_needs_a_token(stack, capsys, monkeypatch):
    import io
    assert cli(["run", "--role", "worker", "--token", "x", "--token-stdin", "--", "true"]) == 2
    monkeypatch.delenv("BURN_JOIN_TOKEN")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli(["run", "--role", "worker", "--token-stdin", "--", "true"]) == 2
    assert "join token is required" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to switch users")
def test_real_process_one_agent_cannot_read_anothers_environment(stack, capfd):
    """A child of a weaker role must not read its parent's credentials, and both run in one container."""
    import tempfile
    import threading
    from burn_cli.runner import privs
    shared = tempfile.mkdtemp()
    os.chmod(shared, 0o777)
    first = f"""
        import os, time
        open({shared + '/pid'!r}, "w").write(str(os.getpid()))
        open({shared + '/uid'!r}, "w").write(str(os.getuid()))
        while not os.path.exists({shared + '/done'!r}):
            time.sleep(0.05)"""
    second = f"""
        import json, os, time
        while not os.path.exists({shared + '/uid'!r}):
            time.sleep(0.05)
        pid = int(open({shared + '/pid'!r}).read())
        try:
            open(f"/proc/{{pid}}/environ", "rb").read(); readable = True
        except OSError:
            readable = False
        open({shared + '/done'!r}, "w").write("x")
        print(json.dumps({{"mine": os.getuid(), "theirs": int(open({shared + '/uid'!r}).read()), "readable": readable}}))"""
    a, b = instance(stack, py(first), drop=privs.resolve(None)), instance(stack, py(second), drop=privs.resolve(None))
    t = threading.Thread(target=a.run)
    t.start()
    assert b.run() == 0
    t.join(10)
    result = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
    assert b.drop.uid != a.drop.uid and result["mine"] == b.drop.uid and result["theirs"] == a.drop.uid
    assert result["readable"] is False

