import pytest

from src.config import Settings, load_settings


def test_defaults_are_loopback_on_port_29400():
    settings = Settings()
    assert (settings.host, settings.port) == ("127.0.0.1", 29400)
    assert settings.voice == "en_US-lessac-medium"


def test_exposed_without_token_only_beyond_loopback():
    assert Settings(host="0.0.0.0", internal_token="").exposed_without_token
    assert not Settings(host="0.0.0.0", internal_token="t").exposed_without_token
    assert not Settings(host="127.0.0.1", internal_token="").exposed_without_token


def test_load_settings_reads_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BARD_PORT", "9123")
    monkeypatch.setenv("BARD_TTL_HOURS", "2")
    monkeypatch.setenv("BARD_QUOTA_MB", "5")
    monkeypatch.setenv("BARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    settings = load_settings()
    assert settings.port == 9123
    assert settings.ttl_seconds == 7200
    assert settings.quota_bytes == 5 * 1024 * 1024
    assert settings.audio_dir == tmp_path / "audio"
    assert settings.internal_token == "secret"


def test_the_default_voice_is_a_piper_voice_unless_one_is_named(monkeypatch, tmp_path):
    monkeypatch.setenv("BARD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BARD_VOICE", raising=False)
    assert load_settings().voice == "en_US-lessac-medium"
    monkeypatch.setenv("BARD_VOICE", "bm_fable")
    assert load_settings().voice == "bm_fable"

