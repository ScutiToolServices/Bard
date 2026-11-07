"""Text to MP3. The speech comes from a TTS_Piper_Model and a TTS_Paradee_Model service chosen on the admin page;
ffmpeg (WAV to MP3) runs here as an asyncio subprocess."""

from __future__ import annotations

import asyncio
import io
import time
import wave
from dataclasses import dataclass
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


class HttpTtsEngine:
    """A TTS_Piper_Model or TTS_Paradee_Model service (AI/ModelServices): `POST /v1/speak`
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
    """Piper or Paradee the way the admin page has it set right now: the model service at the slot's address, or nothing
    yet. The slot is read on every call, so a change on the admin page applies to the next request."""

    def __init__(self, slot: Callable[[], tuple[str, str] | None], ffmpeg: str | Callable[[], str] = "ffmpeg") -> None:
        self._slot, self._ffmpeg = slot, ffmpeg
        self._remote: tuple[tuple[str, str], HttpTtsEngine] | None = None

    def _engine(self) -> HttpTtsEngine | None:
        wanted = self._slot()
        if wanted is None:
            return None
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
            raise EngineFailed("No speech model service is connected. Save a connection on the Bard admin page.")
        return await engine.synthesize(text, voice)


async def probe_voices(url: str, token: str) -> str:
    """The admin page's Test button for a TTS_Piper_Model / TTS_Paradee_Model address: reachable, then token accepted."""
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

    def __init__(self, piper: TtsEngine, paradee: TtsEngine) -> None:
        self._piper = piper
        self._paradee = paradee

    def _owner(self, voice: str) -> TtsEngine:
        return self._paradee if voice in PARADEE_VOICES else self._piper

    def voices(self) -> list[str]:
        return self._piper.voices() + self._paradee.voices()

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        return await self._owner(voice).synthesize(text, voice)


def make_backends(settings: Settings) -> Backends:
    """The model-service slots; addresses and tokens are saved on the admin page only."""
    return Backends(settings.state_dir / "backends.json")


def build_engine(settings: Settings, backends: Backends | None = None) -> TtsEngine:
    """Piper and Paradee, each the model service saved at its slot; with no address saved, calls fail with a hint to
    connect one. The voice of each request picks the engine. The slots are read on every call."""
    backends = backends or make_backends(settings)
    ffmpeg = lambda: settings.ffmpeg  # noqa: E731
    return MultiEngine(SlotEngine(lambda: backends.get("piper"), ffmpeg), SlotEngine(lambda: backends.get("paradee"), ffmpeg))
