import pytest

from src.config import Settings, load_settings


def test_defaults_are_loopback_on_port_29400():
    settings = Settings()
    assert (settings.host, settings.port) == ("127.0.0.1", 29400)
    assert settings.voice == "en_US-lessac-medium"


def test_exposed_without_auth_only_beyond_loopback():
    assert Settings(host="0.0.0.0").exposed_without_auth
    assert not Settings(host="0.0.0.0", admin_password="pw").exposed_without_auth
    assert not Settings(host="127.0.0.1").exposed_without_auth


def test_load_settings_reads_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVICE_PORT", "9123")
    monkeypatch.setenv("SERVICE_TTL_HOURS", "2")
    monkeypatch.setenv("SERVICE_QUOTA_MB", "5")
    monkeypatch.setenv("SERVICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SERVICE_ADMIN_PASSWORD", "secret")
    settings = load_settings()
    assert settings.port == 9123
    assert settings.ttl_seconds == 7200
    assert settings.quota_bytes == 5 * 1024 * 1024
    assert settings.audio_dir == tmp_path / "audio"
    assert settings.admin_password == "secret"


def test_the_default_voice_is_a_piper_voice_unless_one_is_named(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVICE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SERVICE_VOICE", raising=False)
    assert load_settings().voice == "en_US-lessac-medium"
    monkeypatch.setenv("SERVICE_VOICE", "bm_fable")
    assert load_settings().voice == "bm_fable"

