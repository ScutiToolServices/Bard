import io
import shutil
import wave
from pathlib import Path

import pytest

from src.config import Settings
from src.engine import ParadeeEngine, PiperEngine, build_engine
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


def test_paradee_lists_its_four_voices_and_unload_does_nothing():
    engine = ParadeeEngine(model=FakeParadee())
    assert engine.voices() == ["af_heart", "bf_isabella", "am_puck", "bm_fable"]
    engine.unload("af_heart")
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


def test_build_engine_follows_the_setting(tmp_path):
    assert isinstance(build_engine(Settings(data_dir=tmp_path)), PiperEngine)
    assert isinstance(build_engine(Settings(data_dir=tmp_path, engine="paradee")), ParadeeEngine)


@NEEDS_FFMPEG
async def test_real_paradee_synthesis_produces_speech():
    pytest.importorskip("paradee")
    result = await ParadeeEngine().synthesize("Hello from Bard.", "bf_isabella")
    assert result.duration_s > 0.5 and len(result.mp3) > 3000
