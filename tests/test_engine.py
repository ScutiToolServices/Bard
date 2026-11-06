import importlib.util
import io
import shutil
import wave
from pathlib import Path

import httpx
import pytest

from src.config import Settings
import src.engine as engine_module
from src.admin_api import Backends
from src.engine import HttpTtsEngine, MultiEngine, ParadeeEngine, PiperEngine, SlotEngine, build_engine, probe_voices
from src.errors import EngineFailed, VoiceUnavailable

VOICES = Path(__file__).resolve().parent.parent / ".data" / "voices"
NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def silent_wav(seconds: float = 0.5, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def test_voices_lists_onnx_stems_only(tmp_path):
    (tmp_path / "en_US-a.onnx").write_bytes(b"")
    (tmp_path / "en_US-a.onnx.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("")
    assert PiperEngine(tmp_path).voices() == ["en_US-a"]


def test_voices_is_empty_when_the_directory_is_missing(tmp_path):
    assert PiperEngine(tmp_path / "nope").voices() == []


async def test_unknown_voice_is_voice_unavailable(tmp_path):
    with pytest.raises(VoiceUnavailable):
        await PiperEngine(tmp_path).synthesize("hi", "missing")


async def test_missing_ffmpeg_is_engine_failed(tmp_path):
    with pytest.raises(EngineFailed, match="ffmpeg"):
        await PiperEngine(tmp_path, ffmpeg="definitely-not-ffmpeg")._mp3(silent_wav())


@NEEDS_FFMPEG
async def test_mp3_encoding_returns_mp3_bytes(tmp_path):
    mp3 = await PiperEngine(tmp_path)._mp3(silent_wav())
    assert mp3[:3] == b"ID3" or mp3[0] == 0xFF
    assert len(mp3) > 100


@NEEDS_FFMPEG
@pytest.mark.skipif(not (VOICES / "en_US-lessac-medium.onnx").exists(), reason="voice not downloaded")
async def test_real_synthesis_produces_speech():
    result = await PiperEngine(VOICES).synthesize("Hello from Bard.", "en_US-lessac-medium")
    assert result.duration_s > 0.5
    assert len(result.mp3) > 3000


class FakeParadee:
    """Stands in for the Paradee model: half a second of silence at 24 kHz."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, voice="af_heart"):
        import numpy

        self.calls.append((text, voice))
        return numpy.zeros(12000, dtype="float32")


def test_paradee_lists_its_four_voices():
    engine = ParadeeEngine(model=FakeParadee())
    assert engine.voices() == ["af_heart", "bf_isabella", "am_puck", "bm_fable"]
    assert engine.voices()[0] == "af_heart"


async def test_paradee_unknown_voice_is_voice_unavailable():
    with pytest.raises(VoiceUnavailable):
        await ParadeeEngine(model=FakeParadee()).synthesize("hi", "en_US-lessac-medium")


async def test_paradee_wav_is_24khz_and_as_long_as_the_audio():
    model = FakeParadee()
    wav, seconds = await __import__("asyncio").to_thread(ParadeeEngine(model=model)._wav, "Hello.", "bm_fable")
    assert seconds == 0.5 and model.calls == [("Hello.", "bm_fable")]
    with wave.open(io.BytesIO(wav)) as reader:
        assert (reader.getframerate(), reader.getnchannels(), reader.getnframes()) == (24000, 1, 12000)


async def test_paradee_empty_audio_is_engine_failed():
    class Silent(FakeParadee):
        def __call__(self, text, voice="af_heart"):
            import numpy

            return numpy.zeros(0, dtype="float32")

    with pytest.raises(EngineFailed, match="No speech"):
        await ParadeeEngine(model=Silent()).synthesize("...", "af_heart")


@NEEDS_FFMPEG
async def test_paradee_synthesize_returns_mp3_and_duration():
    result = await ParadeeEngine(model=FakeParadee()).synthesize("Hello.", "af_heart")
    assert result.duration_s == 0.5 and (result.mp3[:3] == b"ID3" or result.mp3[0] == 0xFF)


def test_build_engine_lists_paradee_alone_when_it_is_the_chosen_engine(tmp_path):
    assert isinstance(build_engine(Settings(data_dir=tmp_path)), MultiEngine)
    chosen = build_engine(Settings(data_dir=tmp_path, engine="paradee"))
    if importlib.util.find_spec("paradee"):
        assert isinstance(chosen._engine(), ParadeeEngine) and "en_US-lessac-medium" not in chosen.voices()
    else:
        assert isinstance(chosen, MultiEngine)


async def test_multi_engine_lists_both_and_routes_by_voice(tmp_path):
    (tmp_path / "en_US-a.onnx").write_bytes(b"x")
    multi = MultiEngine(PiperEngine(tmp_path), ParadeeEngine(model=FakeParadee()))
    assert multi.voices() == ["en_US-a", "af_heart", "bf_isabella", "am_puck", "bm_fable"]
    with pytest.raises(VoiceUnavailable):
        await multi.synthesize("Hello.", "missing")  # unknown voices go to Piper, which refuses them
    assert MultiEngine(PiperEngine(tmp_path)).voices() == ["en_US-a"]


@NEEDS_FFMPEG
async def test_multi_engine_speaks_a_paradee_voice(tmp_path):
    multi = MultiEngine(PiperEngine(tmp_path), ParadeeEngine(model=FakeParadee()))
    assert (await multi.synthesize("Hello.", "af_heart")).duration_s == 0.5


@NEEDS_FFMPEG
async def test_real_paradee_synthesis_produces_speech():
    pytest.importorskip("paradee")
    result = await ParadeeEngine().synthesize("Hello from Bard.", "bf_isabella")
    assert result.duration_s > 0.5 and len(result.mp3) > 3000


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
    result = await HttpTtsEngine("http://piper:9150/", "ttspiper_x").synthesize("hello", "en_US-a")
    assert seen["url"] == "http://piper:9150/v1/speak" and seen["token"] == "ttspiper_x"
    assert b'"voice":"en_US-a"' in seen["body"].replace(b" ", b"") and b'"text":"hello"' in seen["body"].replace(b" ", b"")
    assert result.mp3.startswith(b"mp3:RIFF") and abs(result.duration_s - 0.5) < 0.01


@pytest.mark.parametrize("status,error", [(403, VoiceUnavailable), (404, VoiceUnavailable), (422, EngineFailed),
                                          (429, EngineFailed), (500, EngineFailed)])
async def test_http_engine_maps_failures_to_safe_errors(monkeypatch, status, error):
    remote(monkeypatch, lambda request: httpx.Response(status, json={"detail": "boom at /secret/path"}))
    with pytest.raises(error) as raised:
        await HttpTtsEngine("http://piper:9150").synthesize("hi", "v")
    assert "secret" not in str(raised.value) and "piper" not in str(raised.value)


async def test_http_engine_unreachable_service_and_bad_audio_are_engine_failed(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused http://piper:9150")

    remote(monkeypatch, refuse)
    with pytest.raises(EngineFailed):
        await HttpTtsEngine("http://piper:9150").synthesize("hi", "v")
    remote(monkeypatch, lambda request: httpx.Response(200, content=b"not a wav"))
    with pytest.raises(EngineFailed):
        await HttpTtsEngine("http://piper:9150").synthesize("hi", "v")


def test_http_engine_lists_the_voices_the_token_may_use_and_keeps_them_a_while(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.headers.get("X-Internal-Token"))
        if len(calls) > 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"voices": ["a", "b"], "default": "a"})

    remote(monkeypatch, handler)
    engine = HttpTtsEngine("http://piper:9150", "t")
    assert engine.voices() == ["a", "b"] and engine.voices() == ["a", "b"] and calls == ["t"]  # second call is from the cache
    engine._voices_at -= 60
    assert engine.voices() == ["a", "b"] and len(calls) == 2  # service failing: the last list stays


def test_http_engine_voices_fail_when_the_service_was_never_reached(monkeypatch):
    remote(monkeypatch, lambda request: httpx.Response(500))
    with pytest.raises(EngineFailed):
        HttpTtsEngine("http://piper:9150").voices()


def test_build_engine_uses_the_model_services_when_their_slots_have_addresses(tmp_path):
    def slots(piper, paradee):
        backends = Backends(tmp_path / "backends.json")
        backends.reset("piper")
        if piper:
            backends.set("piper", "http://p:9150", "")
        if paradee:
            backends.set("paradee", "http://d:9160", "")
        return backends
    both = build_engine(Settings(data_dir=tmp_path), slots(True, True))
    assert isinstance(both, MultiEngine) and isinstance(both._piper._engine(), HttpTtsEngine)
    assert isinstance(both._paradee._engine(), HttpTtsEngine)
    alone = build_engine(Settings(data_dir=tmp_path, engine="paradee"), slots(False, True))
    assert isinstance(alone, SlotEngine) and alone.is_remote
    local_piper = build_engine(Settings(data_dir=tmp_path), slots(False, True))
    assert isinstance(local_piper._piper._engine(), PiperEngine) and isinstance(local_piper._paradee._engine(), HttpTtsEngine)


def test_a_slot_changed_on_the_admin_page_applies_to_the_next_call(tmp_path, monkeypatch):
    backends = Backends(tmp_path / "backends.json")
    local = PiperEngine(tmp_path / "voices")
    slot = SlotEngine(local, lambda: backends.get("piper"))
    assert slot.voices() == [] and slot._engine() is local and not slot.is_remote
    remote(monkeypatch, lambda request: httpx.Response(200, json={"voices": ["x", "y"]}))
    backends.set("piper", "http://piper:9150", "t")
    assert slot.voices() == ["x", "y"] and slot.is_remote and isinstance(slot._engine(), HttpTtsEngine)
    first = slot._engine()
    assert slot._engine() is first  # the same address and token reuse the client
    backends.set("piper", "http://other:9150", None)
    assert slot._engine() is not first
    backends.set("piper", "", None)
    assert slot._engine() is local


async def test_a_slot_with_no_local_engine_says_the_voice_is_not_installed(tmp_path):
    with pytest.raises(VoiceUnavailable):
        await SlotEngine(None, lambda: None).synthesize("hi", "af_heart")
    assert SlotEngine(None, lambda: None).voices() == []


async def test_probe_voices_checks_health_then_the_token(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("X-Internal-Token")))
        return httpx.Response(200, json={"voices": ["a", "b"]} if request.url.path == "/v1/voices" else {"status": "ok"})

    remote(monkeypatch, handler)
    assert await probe_voices("http://p:9150", "tok") == "Connected, token accepted, 2 voice(s)."
    assert seen == [("/health", None), ("/v1/voices", "tok")]
    remote(monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"}))
    with pytest.raises(Exception, match="not a speech model service"):
        await probe_voices("http://p:9150", "tok")
