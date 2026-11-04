"""Speak: prepare the text, reuse a stored file or synthesize one, store it."""

from __future__ import annotations

import asyncio

from src.config import Settings
from src.contract import SpeakResult, VoiceList
from src.engine import TtsEngine
from src.errors import IdentityRequired, VoiceUnavailable
from src.store import AudioStore
from src.text_prep import prepare

_NO_IDENTITY = "No signed-in user was sent with this request."


class BardService:
    def __init__(
        self,
        engine: TtsEngine,
        store: AudioStore,
        settings: Settings,
    ) -> None:
        self._engine = engine
        self._store = store
        self._settings = settings
        self._slots = asyncio.Semaphore(settings.max_concurrent)
        # shortcut: a lock may be dropped while a waiter still holds it, so two identical
        # requests can then both synthesize; harmless (same file id), upgrade if it ever hurts.
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def list_voices(self) -> VoiceList:
        voices = await asyncio.to_thread(self._engine.voices)
        return VoiceList(
            voices=voices,
            default=self._settings.voice,
            message=f"{len(voices)} voice(s) installed; the default is {self._settings.voice}.",
        )

    async def speak(self, owner: str, text: str, voice: str | None = None) -> SpeakResult:
        if not owner:
            raise IdentityRequired(_NO_IDENTITY)
        voice = voice or self._settings.voice
        if voice not in await asyncio.to_thread(self._engine.voices):
            raise VoiceUnavailable(f"Voice '{voice}' is not installed. Call tool_bard_listVoices for the list.")
        clean = prepare(text, self._settings.max_chars)
        file_id = AudioStore.file_id(voice, clean)
        key = (owner, file_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                stored = await asyncio.to_thread(self._store.get, owner, file_id)
                cached = stored is not None
                if stored is None:
                    async with self._slots:
                        synthesis = await self._engine.synthesize(clean, voice)
                    stored = await asyncio.to_thread(
                        self._store.put, owner, file_id, synthesis.mp3, voice, synthesis.duration_s
                    )
        finally:
            if self._locks.get(key) is lock and not lock.locked():
                del self._locks[key]
        note = ", from cache" if cached else ""
        return SpeakResult(
            file_id=file_id,
            voice=voice,
            duration_s=stored.duration_s,
            size_bytes=stored.size_bytes,
            cached=cached,
            message=f"Speech ready ({stored.duration_s:.1f} s, voice {voice}{note}). The player is shown in the chat.",
        )
