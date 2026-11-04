import pytest

from src.config import Settings, load_settings


def test_defaults_are_loopback_on_port_8090():
    settings = Settings()
    assert (settings.host, settings.port) == ("127.0.0.1", 8090)
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
    assert settings.voices_dir == tmp_path / "voices"
    assert settings.audio_dir == tmp_path / "audio"
    assert settings.internal_token == "secret"


def test_engine_defaults_to_piper_and_paradee_brings_its_own_default_voice(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.load_dotenv", lambda *args, **kwargs: None)  # the real .env must not decide this test
    monkeypatch.setenv("BARD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BARD_ENGINE", raising=False)
    monkeypatch.delenv("BARD_VOICE", raising=False)
    assert (load_settings().engine, load_settings().voice) == ("piper", "en_US-lessac-medium")
    monkeypatch.setenv("BARD_ENGINE", " Paradee ")
    assert (load_settings().engine, load_settings().voice) == ("paradee", "af_heart")
    monkeypatch.setenv("BARD_VOICE", "bm_fable")
    assert load_settings().voice == "bm_fable"


def test_a_piper_voice_name_is_refused_for_paradee(monkeypatch):
    monkeypatch.setattr("src.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("BARD_ENGINE", "paradee")
    monkeypatch.setenv("BARD_VOICE", "en_US-lessac-medium")
    with pytest.raises(ValueError, match="not a Paradee voice"):
        load_settings()


def test_an_unknown_engine_is_refused(monkeypatch):
    monkeypatch.setattr("src.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("BARD_ENGINE", "kokoro")
    with pytest.raises(ValueError, match="BARD_ENGINE"):
        load_settings()


def test_model_service_urls_and_tokens_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("BARD_PIPER_URL", "http://p:9150")
    monkeypatch.setenv("BARD_PIPER_TOKEN", "ttspiper_x")
    monkeypatch.setenv("BARD_PARADEE_URL", "http://d:9160")
    monkeypatch.setenv("BARD_PARADEE_TOKEN", "ttsparadee_y")
    loaded = load_settings()
    assert (loaded.piper_url, loaded.piper_token, loaded.paradee_url, loaded.paradee_token) == (
        "http://p:9150", "ttspiper_x", "http://d:9160", "ttsparadee_y")
