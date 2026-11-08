import pytest

from fake_api import API_TOKEN, FakeApi


@pytest.fixture(autouse=True)
def isolated_sign_in(monkeypatch, tmp_path_factory):
    """No test reads or writes the real ~/.config/burn, and the fakes below are local development servers."""
    monkeypatch.setenv("BURN_CONFIG_DIR", str(tmp_path_factory.mktemp("burn-config")))
    monkeypatch.setenv("BURN_LOCAL_DEV", "1")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    api = FakeApi()
    monkeypatch.setenv("BURN_API_URL", api.url)
    monkeypatch.setenv("BURN_DEMO_TOKEN", API_TOKEN)
    monkeypatch.chdir(tmp_path)
    yield api
    api.close()
