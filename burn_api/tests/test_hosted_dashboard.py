"""The hosted dashboard's build and checks: never publish a demo token, never print one."""
import pathlib
import secrets
import subprocess
import sys

import pytest
from botocore.exceptions import ClientError
from burn_contracts import names

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))
import hosted_dashboard  # noqa: E402
from hosted_dashboard import Refused  # noqa: E402

HOSTED = "https://d111111abcdef8.cloudfront.net"
SIGNED_IN = {
    names.PEOPLE_ENABLED_PARAM: "on",
    "/burn/api/url": "https://abc123.execute-api.us-east-1.amazonaws.com",
    names.PEOPLE_DOMAIN_PARAM: "https://burn-people-000000000000.auth.us-east-1.amazoncognito.com",
    names.PEOPLE_CLIENT_ID_PARAM: "publicclient",
}


class FakeSsm:
    def __init__(self, values: dict[str, str]):
        self.values = values

    def get_parameter(self, Name):
        if Name not in self.values:
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return {"Parameter": {"Value": self.values[Name]}}


class FakeSecrets:
    def __init__(self, token: str | None):
        self.token = token

    def get_secret_value(self, SecretId):
        assert SecretId == names.DEMO_TOKEN_SECRET
        if self.token is None:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetSecretValue")
        return {"SecretString": self.token}


def token() -> str:
    return secrets.token_urlsafe(30)  # 40 characters, like the deployed demo token


def fake_dist(root: pathlib.Path, bundle: str) -> pathlib.Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><script type="module" src="/assets/index-9b2d4e.js"></script>')
    (dist / "assets" / "index-9b2d4e.js").write_text(bundle)
    return dist


# --- the leak check -----------------------------------------------------------------------------

CLEAN_BUNDLE = ('const c={burnUrl:"https://abc123.execute-api.us-east-1.amazonaws.com",demoToken:""};'
                'function h(t,e){t.delete("x-burn-demo-token");e.demoToken&&t.set("x-burn-demo-token",e.demoToken??"")}'
                't.set("x-burn-demo-token","")')


def test_a_build_without_a_token_passes(tmp_path):
    assert hosted_dashboard.find_leaks(fake_dist(tmp_path, CLEAN_BUNDLE), {token()}) == []


def test_a_build_holding_the_token_fails_without_naming_it(tmp_path):
    value = token()
    dist = fake_dist(tmp_path, f'const c={{demoToken:"{value}".trim()}};')
    leaks = hosted_dashboard.find_leaks(dist, {value, token()})
    assert leaks == ["assets/index-9b2d4e.js: holds the demo token"]
    assert value not in "".join(leaks)


@pytest.mark.parametrize("bundle", [
    'fetch(u,{headers:{"x-burn-demo-token":"anything"}})',
    't.set("x-burn-demo-token", "anything")',
    "t.set('X-Burn-Demo-Token','anything')",
    'const e={BASE_URL:"/",VITE_BURN_DEMO_TOKEN:"anything"}',
])
def test_the_header_or_variable_set_to_any_value_fails(tmp_path, bundle):
    # Even a value no .env file or secret knows about.
    leaks = hosted_dashboard.find_leaks(fake_dist(tmp_path, bundle), set())
    assert leaks == [f"assets/index-9b2d4e.js: sets {names.DEMO_TOKEN_HEADER} or VITE_BURN_DEMO_TOKEN to a value"]


def test_every_file_is_checked_not_only_javascript(tmp_path):
    value = token()
    dist = fake_dist(tmp_path, CLEAN_BUNDLE)
    (dist / "assets" / "index-9b2d4e.js.map").write_text(f'{{"sourcesContent":["{value}"]}}')
    assert hosted_dashboard.find_leaks(dist, {value}) == ["assets/index-9b2d4e.js.map: holds the demo token"]


def test_no_build_is_refused(tmp_path):
    with pytest.raises(Refused, match="was not built"):
        hosted_dashboard.find_leaks(tmp_path / "dist", set())


def test_tokens_come_from_the_env_files_and_the_deployed_secret(tmp_path):
    root_token, local_token, prod_token, secret_token = token(), token(), token(), token()
    (tmp_path / ".env").write_text(f"BURN_API_URL=https://x.invalid\nBURN_DEMO_TOKEN={root_token}\n")
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / ".env.local").write_text(f'VITE_BURN_DEMO_TOKEN="{local_token}"\n')
    (tmp_path / "dashboard" / ".env.production").write_text(f"export VITE_BURN_DEMO_TOKEN='{prod_token}'\n")
    (tmp_path / "dashboard" / ".env.example").write_text("VITE_BURN_DEMO_TOKEN=\n")
    assert hosted_dashboard.demo_tokens(tmp_path, FakeSecrets(secret_token)) == {
        root_token, local_token, prod_token, secret_token}
    assert hosted_dashboard.demo_tokens(tmp_path, FakeSecrets(None)) == {root_token, local_token, prod_token}


# --- build --------------------------------------------------------------------------------------

def make_repo(tmp_path: pathlib.Path, local_token: str) -> pathlib.Path:
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / ".env.local").write_text(f"VITE_BURN_DEMO_TOKEN={local_token}\nVITE_BURN_LOCAL_DEV=1\n")
    return tmp_path


def fake_npm(writes: str | None = None):
    """Stands in for subprocess.run: records each command and, on `npm run build`, writes a dist."""
    calls = []

    def run(command, cwd, env, check):
        calls.append((command, pathlib.Path(cwd), env))
        if command == ["npm", "run", "build"]:
            fake_dist(pathlib.Path(cwd), writes if writes is not None else CLEAN_BUNDLE)
        return subprocess.CompletedProcess(command, 0)
    return run, calls


def test_build_refuses_while_sign_in_is_off(tmp_path):
    run, calls = fake_npm()
    repo = make_repo(tmp_path, token())
    for values in ({**SIGNED_IN, names.PEOPLE_ENABLED_PARAM: "off"},
                   {k: v for k, v in SIGNED_IN.items() if k != names.PEOPLE_ENABLED_PARAM}):
        with pytest.raises(Refused, match="sign-in is not on"):
            hosted_dashboard.build(FakeSsm(values), FakeSecrets(None), repo, run)
    assert calls == []  # nothing was built


def test_build_refuses_when_a_sign_in_value_is_missing(tmp_path):
    run, calls = fake_npm()
    values = {k: v for k, v in SIGNED_IN.items() if k != names.PEOPLE_CLIENT_ID_PARAM}
    with pytest.raises(Refused, match=names.PEOPLE_CLIENT_ID_PARAM):
        hosted_dashboard.build(FakeSsm(values), FakeSecrets(None), make_repo(tmp_path, token()), run)
    assert calls == []


def test_build_uses_the_deployed_values_and_blanks_the_token_in_the_process_env(tmp_path):
    local_token = token()
    repo = make_repo(tmp_path, local_token)
    run, calls = fake_npm()
    hosted_dashboard.build(FakeSsm(SIGNED_IN), FakeSecrets(None), repo, run)
    assert [command for command, _, _ in calls] == [["npm", "ci"], ["npm", "run", "build"]]
    for _, cwd, env in calls:
        assert cwd == repo / "dashboard"
        assert env["VITE_BURN_API_URL"] == SIGNED_IN["/burn/api/url"]
        assert env["VITE_BURN_PEOPLE_DOMAIN"] == SIGNED_IN[names.PEOPLE_DOMAIN_PARAM]
        assert env["VITE_BURN_PEOPLE_CLIENT_ID"] == SIGNED_IN[names.PEOPLE_CLIENT_ID_PARAM]
        assert env["VITE_BURN_LOCAL_DEV"] == "0"
        assert env["VITE_BURN_DEMO_TOKEN"] == ""  # set and empty: beats .env.local in Vite
        assert local_token not in "".join(env.values())


def test_build_fails_when_the_bundle_still_holds_a_token(tmp_path, monkeypatch, capsys):
    local_token = token()
    repo = make_repo(tmp_path, local_token)
    run, calls = fake_npm(writes=f'const c={{demoToken:"{local_token}"}};')
    monkeypatch.setattr(hosted_dashboard, "REPO", repo)
    monkeypatch.setattr(hosted_dashboard.subprocess, "run", run)
    monkeypatch.setattr(hosted_dashboard.boto3, "client", lambda service, **kwargs: (
        FakeSsm(SIGNED_IN) if service == "ssm" else FakeSecrets(None)))
    assert hosted_dashboard.main(["build"]) == 1
    assert len(calls) == 2
    out = capsys.readouterr()
    assert "refusing: the dashboard build holds a demo token" in out.err
    assert "assets/index-9b2d4e.js: holds the demo token" in out.err
    assert local_token not in out.out + out.err


# --- check-hosted and url -------------------------------------------------------------------------

def test_check_hosted_refuses_until_the_dashboard_is_deployed():
    with pytest.raises(Refused, match="run make dashboard-deploy first"):
        hosted_dashboard.check_hosted(FakeSsm(SIGNED_IN), fetch=lambda url: True)


def test_check_hosted_refuses_while_the_distribution_is_empty():
    ssm = FakeSsm({**SIGNED_IN, names.DASHBOARD_URL_PARAM: HOSTED})
    with pytest.raises(Refused, match="does not serve a page yet: run make dashboard-deploy first"):
        hosted_dashboard.check_hosted(ssm, fetch=lambda url: False)


def test_check_hosted_refuses_while_sign_in_is_off():
    ssm = FakeSsm({**SIGNED_IN, names.PEOPLE_ENABLED_PARAM: "off", names.DASHBOARD_URL_PARAM: HOSTED})
    with pytest.raises(Refused, match="sign-in is not on"):
        hosted_dashboard.check_hosted(ssm, fetch=lambda url: True)


def test_check_hosted_passes_once_the_dashboard_serves_its_root():
    fetched = []
    ssm = FakeSsm({**SIGNED_IN, names.DASHBOARD_URL_PARAM: HOSTED})
    hosted_dashboard.check_hosted(ssm, fetch=lambda url: fetched.append(url) or True)
    assert fetched == [HOSTED + "/"]


def test_url_is_the_hosted_address_only_once_it_is_live():
    assert hosted_dashboard.live_url(FakeSsm({}), fetch=lambda url: True) == ""
    ssm = FakeSsm({names.DASHBOARD_URL_PARAM: HOSTED})
    assert hosted_dashboard.live_url(ssm, fetch=lambda url: False) == ""
    assert hosted_dashboard.live_url(ssm, fetch=lambda url: True) == HOSTED


def test_only_https_addresses_are_fetched():
    assert hosted_dashboard.serves_a_page("file:///etc/passwd") is False
    assert hosted_dashboard.serves_a_page("http://example.invalid/") is False
