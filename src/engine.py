"""Text to MP3. Piper and Paradee are CPU-bound and blocking, so they run on a worker thread;
ffmpeg runs as an asyncio subprocess."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import httpx

from src.admin_api import Backends, ProbeError, check
from src.config import PARADEE_VOICES, Settings
from src.runtime import as_getter
from src.errors import EngineFailed, VoiceUnavailable


@dataclass(frozen=True)
class Synthesis:
    mp3: bytes
    duration_s: float


class TtsEngine(Protocol):
    def voices(self) -> list[str]: ...

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
    model file, so they cannot be installed one by one. The model downloads from Hugging Face on first use.
    `model` is injectable for tests: a callable `(text, voice=...)` returning float audio at 24 kHz."""

    def __init__(self, ffmpeg: str | Callable[[], str] = "ffmpeg", version: str = "1.2", model: object | None = None) -> None:
        self._ffmpeg = as_getter(ffmpeg)
        self._version = version
        self._model_obj = model
        self._load_lock = threading.Lock()

    def voices(self) -> list[str]:
        return list(PARADEE_VOICES)

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


class HttpTtsEngine:
    """A TTSPiperModel or TTSParadeeModel service (AI/ModelServices) in place of the in-process engine: `POST /v1/speak`
    returns WAV, `GET /v1/voices` lists the voices the token may use. ffmpeg stays here (WAV to MP3).
    Needs a token when the service has an admin password; the token's "projects" list limits the voices.

    shortcut: one new connection per call, and the voice list is kept 30 s; upgrade if call volume grows."""

    VOICES_TTL_S = 30.0

    def __init__(self, url: str, token: str = "", ffmpeg: str | Callable[[], str] = "ffmpeg", timeout_s: float = 60.0) -> None:
        self._url = url.rstrip("/")
        self._headers = {"X-Internal-Token": token} if token else {}
        self._ffmpeg = as_getter(ffmpeg)
        self._timeout_s = timeout_s
        self._voices: list[str] | None = None
        self._voices_at = 0.0

    def voices(self) -> list[str]:
        """Blocking (Bard calls it through asyncio.to_thread). Keeps the last list when the service is briefly away."""
        if self._voices is not None and time.monotonic() - self._voices_at < self.VOICES_TTL_S:
            return list(self._voices)
        try:
            reply = httpx.get(f"{self._url}/v1/voices", headers=self._headers, timeout=10.0)
            reply.raise_for_status()
            self._voices = [str(name) for name in reply.json()["voices"]]
            self._voices_at = time.monotonic()
        except Exception as error:  # never leak the URL or the service's text
            if self._voices is None:
                raise EngineFailed("The speech service could not be reached.") from error
        return list(self._voices)

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                reply = await http.post(f"{self._url}/v1/speak", json={"text": text, "voice": voice}, headers=self._headers)
        except httpx.HTTPError as error:
            raise EngineFailed("The speech service could not be reached.") from error
        if reply.status_code in (403, 404):
            raise VoiceUnavailable(f"Voice '{voice}' is not installed.")
        if reply.status_code == 422:
            raise EngineFailed("No speech could be made from that text.")
        if reply.status_code != 200:
            raise EngineFailed("The speech service could not make that audio. Try again.")
        try:
            with wave.open(io.BytesIO(reply.content)) as wav:
                seconds = wav.getnframes() / wav.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError) as error:
            raise EngineFailed("The speech service returned audio Bard cannot read.") from error
        return Synthesis(await encode_mp3(reply.content, self._ffmpeg()), seconds)


class SlotEngine:
    """Piper or Paradee the way the admin page has it set right now: the model service at the slot's URL, or `local`
    (the library in this process; None when it is not installed). The slot is read on every call, so a change on the
    admin page applies to the next request."""

    def __init__(self, local: TtsEngine | None, slot: Callable[[], tuple[str, str] | None],
                 ffmpeg: str | Callable[[], str] = "ffmpeg") -> None:
        self._local, self._slot, self._ffmpeg = local, slot, ffmpeg
        self._remote: tuple[tuple[str, str], HttpTtsEngine] | None = None

    def _engine(self) -> TtsEngine | None:
        wanted = self._slot()
        if wanted is None:
            return self._local
        if self._remote is None or self._remote[0] != wanted:
            self._remote = (wanted, HttpTtsEngine(wanted[0], wanted[1], self._ffmpeg))
        return self._remote[1]

    @property
    def is_remote(self) -> bool:
        return self._slot() is not None

    def voices(self) -> list[str]:
        engine = self._engine()
        return engine.voices() if engine is not None else []

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        engine = self._engine()
        if engine is None:
            raise VoiceUnavailable(f"Voice '{voice}' is not installed.")
        return await engine.synthesize(text, voice)


async def probe_voices(url: str, token: str) -> str:
    """The admin page's Test button for a TTSPiperModel / TTSParadeeModel address: reachable, then token accepted."""
    await check(url, "", "/health")
    reply = await check(url, token, "/v1/voices")
    try:
        names = reply.json()["voices"]
    except (ValueError, KeyError, TypeError):
        raise ProbeError("That address is not a speech model service.") from None
    return f"Connected, token accepted, {len(names)} voice(s)."


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

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        return await self._owner(voice).synthesize(text, voice)


def make_backends(settings: Settings) -> Backends:
    """The model-service slots; addresses and tokens are saved on the admin page only."""
    return Backends(settings.state_dir / "backends.json")


def build_engine(settings: Settings, backends: Backends | None = None) -> TtsEngine:
    """Each engine is the model service at its slot when the slot has a URL, otherwise the library run in this process.
    BARD_ENGINE=paradee serves Paradee alone (its four voices) when it is available; otherwise Piper plus Paradee.
    The choice of engines is made at start; the slots are read on every call."""
    backends = backends or make_backends(settings)
    ffmpeg = lambda: settings.ffmpeg  # noqa: E731
    installed = importlib.util.find_spec("paradee") is not None
    paradee = SlotEngine(ParadeeEngine(ffmpeg) if installed else None, lambda: backends.get("paradee"), ffmpeg)
    if settings.engine == "paradee" and (installed or paradee.is_remote):
        return paradee
    return MultiEngine(SlotEngine(PiperEngine(settings.voices_dir, ffmpeg), lambda: backends.get("piper"), ffmpeg), paradee)
