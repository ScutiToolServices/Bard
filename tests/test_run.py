import pytest

from src import run


def test_refuses_to_start_exposed_without_an_admin_password(monkeypatch):
    monkeypatch.setenv("SERVICE_HOST", "0.0.0.0")
    monkeypatch.setenv("SERVICE_ADMIN_PASSWORD", "")
    with pytest.raises(SystemExit, match="SERVICE_ADMIN_PASSWORD"):
        run.main()
