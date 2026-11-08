import pytest

from fake_api import API_TOKEN, FakeApi


@pytest.fixture(autouse=True)
def isolated_sign_in(monkeypatch, tmp_path_factory):
    """No test reads or writes the real ~/.config/burn or the repo's .env, or asks AWS whether sign-in is on, and
    the fakes below are local development servers."""
    from burn_cli import config
    from burn_contracts import automation

    def no_aws(service):
        raise RuntimeError("tests make no AWS calls")

    monkeypatch.setenv("BURN_CONFIG_DIR", str(tmp_path_factory.mktemp("burn-config")))
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path_factory.mktemp("repo"))  # no .env there
    monkeypatch.setattr(automation, "_default", automation.AutomationPerson(client=no_aws))
    monkeypatch.delenv("BURN_PEOPLE_ENABLED", raising=False)
    monkeypatch.setenv("BURN_LOCAL_DEV", "1")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    api = FakeApi()
    monkeypatch.setenv("BURN_API_URL", api.url)
    monkeypatch.setenv("BURN_DEMO_TOKEN", API_TOKEN)
    monkeypatch.chdir(tmp_path)
    yield api
    api.close()
