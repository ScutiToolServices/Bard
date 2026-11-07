import io
import shutil
import wave

import httpx
import pytest

from src.config import Settings
import src.engine as engine_module
from src.admin_api import Backends
from src.engine import HttpTtsEngine, MultiEngine, SlotEngine, build_engine, encode_mp3, probe_voices
from src.errors import EngineFailed, VoiceUnavailable

NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def silent_wav(seconds: float = 0.5, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


async def test_missing_ffmpeg_is_engine_failed():
    with pytest.raises(EngineFailed, match="ffmpeg"):
        await encode_mp3(silent_wav(), "definitely-not-ffmpeg")


@NEEDS_FFMPEG
async def test_mp3_encoding_returns_mp3_bytes():
    mp3 = await encode_mp3(silent_wav(), "ffmpeg")
    assert mp3[:3] == b"ID3" or mp3[0] == 0xFF
    assert len(mp3) > 100


REAL_CLIENT = httpx.AsyncClient  # captured at import: remote() may run twice in one test


def remote(monkeypatch, handler):
    """HttpTtsEngine's clients, answered by `handler` instead of the network; ffmpeg is replaced by a stub."""
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Client(transport=httpx.MockTransport(handler)).get(url, **kwargs))

    async def fake_mp3(wav, ffmpeg):
        return b"mp3:" + wav[:4]

    monkeypatch.setattr(engine_module, "encode_mp3", fake_mp3)


async def test_http_engine_posts_text_and_voice_with_the_token_and_returns_mp3(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), token=request.headers["X-Internal-Token"], body=request.content)
        return httpx.Response(200, content=silent_wav(0.5), headers={"content-type": "audio/wav"})

    remote(monkeypatch, handler)
    result = await HttpTtsEngine("http://piper:29320/", "ttspiper_x").synthesize("hello", "en_US-a")
    assert seen["url"] == "http://piper:29320/v1/speak" and seen["token"] == "ttspiper_x"
    assert b'"voice":"en_US-a"' in seen["body"].replace(b" ", b"") and b'"text":"hello"' in seen["body"].replace(b" ", b"")
    assert result.mp3.startswith(b"mp3:RIFF") and abs(result.duration_s - 0.5) < 0.01


@pytest.mark.parametrize("status,error", [(403, VoiceUnavailable), (404, VoiceUnavailable), (422, EngineFailed),
                                          (429, EngineFailed), (500, EngineFailed)])
async def test_http_engine_maps_failures_to_safe_errors(monkeypatch, status, error):
    remote(monkeypatch, lambda request: httpx.Response(status, json={"detail": "boom at /secret/path"}))
    with pytest.raises(error) as raised:
        await HttpTtsEngine("http://piper:29320").synthesize("hi", "v")
    assert "secret" not in str(raised.value) and "piper" not in str(raised.value)


async def test_http_engine_unreachable_service_and_bad_audio_are_engine_failed(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused http://piper:29320")

    remote(monkeypatch, refuse)
    with pytest.raises(EngineFailed):
        await HttpTtsEngine("http://piper:29320").synthesize("hi", "v")
    remote(monkeypatch, lambda request: httpx.Response(200, content=b"not a wav"))
    with pytest.raises(EngineFailed):
        await HttpTtsEngine("http://piper:29320").synthesize("hi", "v")


def test_http_engine_lists_the_voices_the_token_may_use_and_keeps_them_a_while(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.headers.get("X-Internal-Token"))
        if len(calls) > 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"voices": ["a", "b"], "default": "a"})

    remote(monkeypatch, handler)
    engine = HttpTtsEngine("http://piper:29320", "t")
    assert engine.voices() == ["a", "b"] and engine.voices() == ["a", "b"] and calls == ["t"]  # second call is from the cache
    engine._voices_at -= 60
    assert engine.voices() == ["a", "b"] and len(calls) == 2  # service failing: the last list stays


def test_http_engine_voices_fail_when_the_service_was_never_reached(monkeypatch):
    remote(monkeypatch, lambda request: httpx.Response(500))
    with pytest.raises(EngineFailed):
        HttpTtsEngine("http://piper:29320").voices()


def test_build_engine_uses_the_model_services_saved_at_their_slots(tmp_path):
    backends = Backends(tmp_path / "backends.json")
    nothing = build_engine(Settings(data_dir=tmp_path), backends)
    assert isinstance(nothing, MultiEngine) and nothing.voices() == []
    backends.set("piper", "http://p:29320", "")
    backends.set("paradee", "http://d:29321", "")
    both = build_engine(Settings(data_dir=tmp_path), backends)
    assert isinstance(both._piper._engine(), HttpTtsEngine) and isinstance(both._paradee._engine(), HttpTtsEngine)


async def test_the_voice_picks_the_slot(tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(200, content=silent_wav())

    remote(monkeypatch, handler)
    backends = Backends(tmp_path / "backends.json")
    backends.set("piper", "http://piper:29320", "")
    backends.set("paradee", "http://paradee:29321", "")
    engine = build_engine(Settings(data_dir=tmp_path), backends)
    await engine.synthesize("Hello.", "en_US-a")
    await engine.synthesize("Hello.", "af_heart")
    assert seen == ["piper", "paradee"]


def test_a_slot_changed_on_the_admin_page_applies_to_the_next_call(tmp_path, monkeypatch):
    backends = Backends(tmp_path / "backends.json")
    slot = SlotEngine(lambda: backends.get("piper"))
    assert slot.voices() == [] and slot._engine() is None and not slot.is_remote
    remote(monkeypatch, lambda request: httpx.Response(200, json={"voices": ["x", "y"]}))
    backends.set("piper", "http://piper:29320", "t")
    assert slot.voices() == ["x", "y"] and slot.is_remote and isinstance(slot._engine(), HttpTtsEngine)
    first = slot._engine()
    assert slot._engine() is first  # the same address and token reuse the client
    backends.set("piper", "http://other:29320", None)
    assert slot._engine() is not first
    backends.reset("piper")
    assert slot._engine() is None


async def test_a_slot_with_no_connection_says_to_connect_one():
    with pytest.raises(EngineFailed, match="admin page"):
        await SlotEngine(lambda: None).synthesize("hi", "af_heart")
    assert SlotEngine(lambda: None).voices() == []


async def test_probe_voices_checks_health_then_the_token(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("X-Internal-Token")))
        return httpx.Response(200, json={"voices": ["a", "b"]} if request.url.path == "/v1/voices" else {"status": "ok"})

    remote(monkeypatch, handler)
    assert await probe_voices("http://p:29320", "tok") == "Connected, token accepted, 2 voice(s)."
    assert seen == [("/health", None), ("/v1/voices", "tok")]
    remote(monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"}))
    with pytest.raises(Exception, match="not a speech model service"):
        await probe_voices("http://p:29320", "tok")
