"""Text to MP3. Piper and Paradee are CPU-bound and blocking, so they run on a worker thread;
ffmpeg runs as an asyncio subprocess."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.config import PARADEE_VOICES, Settings
from src.runtime import as_getter
from src.errors import EngineFailed, VoiceUnavailable


@dataclass(frozen=True)
class Synthesis:
    mp3: bytes
    duration_s: float


class TtsEngine(Protocol):
    def voices(self) -> list[str]: ...

    def unload(self, name: str) -> None: ...

    async def synthesize(self, text: str, voice: str) -> Synthesis: ...


async def encode_mp3(wav: bytes, ffmpeg: str) -> bytes:
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
            "-codec:a", "libmp3lame", "-q:a", "4", "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise EngineFailed("ffmpeg was not found on the Bard host.") from error
    out, err = await proc.communicate(wav)
    if proc.returncode != 0:
        raise EngineFailed(f"ffmpeg could not encode the audio: {err.decode('utf-8', 'replace').strip()[:200]}")
    return out


class PiperEngine:
    def __init__(self, voices_dir: Path, ffmpeg: str | Callable[[], str] = "ffmpeg") -> None:
        self._dir = Path(voices_dir)
        self._ffmpeg = as_getter(ffmpeg)
        self._loaded: dict[str, object] = {}
        self._load_lock = threading.Lock()

    def voices(self) -> list[str]:
        return sorted(p.name.removesuffix(".onnx") for p in self._dir.glob("*.onnx")) if self._dir.is_dir() else []

    def unload(self, name: str) -> None:
        """Drop a loaded voice so its files can be deleted."""
        with self._load_lock:
            self._loaded.pop(name, None)

    def _voice(self, name: str):
        with self._load_lock:
            if name not in self._loaded:
                path = self._dir / f"{name}.onnx"
                if not path.is_file():
                    raise VoiceUnavailable(f"Voice '{name}' is not installed.")
                from piper import PiperVoice  # heavy import, only when a voice is first used

                self._loaded[name] = PiperVoice.load(path)
            return self._loaded[name]

    def _wav(self, text: str, name: str) -> tuple[bytes, float]:
        voice = self._voice(name)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav)
            seconds = wav.getnframes() / wav.getframerate()
        return buf.getvalue(), seconds

    async def _mp3(self, wav: bytes) -> bytes:
        return await encode_mp3(wav, self._ffmpeg())

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        wav, seconds = await asyncio.to_thread(self._wav, text, voice)
        return Synthesis(await self._mp3(wav), seconds)


PARADEE_RATE = 24000


class ParadeeEngine:
    """Paradee (an 8M-parameter model distilled from Kokoro) run in this process. Its four voices live in one
    model file, so they cannot be installed or deleted one by one. The model downloads from Hugging Face on first use.
    `model` is injectable for tests: a callable `(text, voice=...)` returning float audio at 24 kHz."""

    def __init__(self, ffmpeg: str | Callable[[], str] = "ffmpeg", version: str = "1.2", model: object | None = None) -> None:
        self._ffmpeg = as_getter(ffmpeg)
        self._version = version
        self._model_obj = model
        self._load_lock = threading.Lock()

    def voices(self) -> list[str]:
        return list(PARADEE_VOICES)

    def unload(self, name: str) -> None:
        """Nothing to drop: the voices are part of the one loaded model."""

    def _model(self):
        with self._load_lock:
            if self._model_obj is None:
                from paradee import Paradee  # heavy import, only when the first text is spoken

                model = Paradee(version=self._version, threads=1)
                for voice in PARADEE_VOICES[:2]:
                    model("Warm up.", voice=voice)  # builds the American and British phonemizers now
                self._model_obj = model
            return self._model_obj

    def _wav(self, text: str, name: str) -> tuple[bytes, float]:
        import numpy

        audio = self._model()(text, voice=name)
        if len(audio) == 0:
            raise EngineFailed("No speech could be made from that text.")
        pcm = (numpy.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(PARADEE_RATE)
            wav.writeframes(pcm.tobytes())
        return buf.getvalue(), len(pcm) / PARADEE_RATE

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        if voice not in PARADEE_VOICES:
            raise VoiceUnavailable(f"Voice '{voice}' is not installed.")
        wav, seconds = await asyncio.to_thread(self._wav, text, voice)
        return Synthesis(await encode_mp3(wav, self._ffmpeg()), seconds)


class MultiEngine:
    """Routes each request to the engine that owns its voice, so the caller picks the engine by picking a voice.
    Voice names never overlap (Paradee's are fixed and lack the Piper `lang_REGION-name-quality` shape)."""

    def __init__(self, piper: TtsEngine, paradee: TtsEngine | None = None) -> None:
        self._piper = piper
        self._paradee = paradee

    def _owner(self, voice: str) -> TtsEngine:
        return self._paradee if self._paradee is not None and voice in PARADEE_VOICES else self._piper

    def voices(self) -> list[str]:
        return self._piper.voices() + (self._paradee.voices() if self._paradee is not None else [])

    def unload(self, name: str) -> None:
        self._owner(name).unload(name)

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        return await self._owner(voice).synthesize(text, voice)


def build_engine(settings: Settings) -> TtsEngine:
    """Piper always; Paradee too when its optional `paradee` extra is installed."""
    paradee = ParadeeEngine(lambda: settings.ffmpeg) if importlib.util.find_spec("paradee") else None
    return MultiEngine(PiperEngine(settings.voices_dir, lambda: settings.ffmpeg), paradee)
