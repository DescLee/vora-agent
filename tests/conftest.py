import pytest


@pytest.fixture(autouse=True)
def default_test_env(monkeypatch) -> None:
    monkeypatch.setenv("MANUS_DISABLE_LOGGING", "1")
