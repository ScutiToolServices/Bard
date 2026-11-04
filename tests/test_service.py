import asyncio

import pytest

from src.config import Settings
from src.errors import IdentityRequired, TextRejected, VoiceUnavailable
from src.service import BardService
from src.store import AudioStore
from tests.fakes import FakeEngine


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def service(engine, tmp_path):
    settings = Settings(data_dir=tmp_path, voice="v1", max_chars=50)
    return BardService(engine, AudioStore(tmp_path / "audio", 60, 10_000), settings)


async def test_speak_synthesizes_stores_and_describes(service, engine):
    result = await service.speak("ann", "Hello **world**", None)
    assert engine.calls == [("Hello world", "v1")]
    assert result.voice == "v1" and result.duration_s == 2.0 and result.cached is False
    assert result.size_bytes == len(b"mp3:Hello world")
    assert "Speech ready" in result.message and "v1" in result.message


async def test_the_second_identical_request_is_a_cache_hit(service, engine):
    first = await service.speak("ann", "Hello", None)
    second = await service.speak("ann", "Hello", None)
    assert second.cached is True and second.file_id == first.file_id
    assert len(engine.calls) == 1


async def test_identical_concurrent_requests_synthesize_once(service, engine):
    await asyncio.gather(service.speak("ann", "same text", None), service.speak("ann", "same text", None))
    assert len(engine.calls) == 1


async def test_owners_do_not_share_audio(service, engine):
    await service.speak("ann", "Hello", None)
    await service.speak("bob", "Hello", None)
    assert len(engine.calls) == 2


async def test_an_explicit_voice_is_used(service, engine):
    result = await service.speak("ann", "Hello", "v2")
    assert result.voice == "v2" and engine.calls == [("Hello", "v2")]


async def test_missing_identity_is_refused_before_any_work(service, engine):
    with pytest.raises(IdentityRequired):
        await service.speak("", "Hello", None)
    assert engine.calls == []


async def test_unknown_voice_is_refused_before_any_work(service, engine):
    with pytest.raises(VoiceUnavailable):
        await service.speak("ann", "Hello", "nope")
    assert engine.calls == []


async def test_text_over_the_limit_is_rejected(service, engine):
    with pytest.raises(TextRejected):
        await service.speak("ann", "x" * 51, None)
    assert engine.calls == []


async def test_list_voices_reports_voices_and_the_default(service):
    result = await service.list_voices()
    assert result.voices == ["v1", "v2"] and result.default == "v1"
    assert "v1" in result.message
