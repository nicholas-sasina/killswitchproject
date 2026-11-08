import pytest

from fake_api import API_TOKEN, FakeApi


@pytest.fixture
def fake(monkeypatch, tmp_path):
    api = FakeApi()
    monkeypatch.setenv("BURN_API_URL", api.url)
    monkeypatch.setenv("BURN_DEMO_TOKEN", API_TOKEN)
    monkeypatch.chdir(tmp_path)
    yield api
    api.close()
