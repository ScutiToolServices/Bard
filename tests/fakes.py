import asyncio

from src.engine import Synthesis


class FakeEngine:
    """Stands in for Piper and ffmpeg: the 'mp3' is the text behind a prefix."""

    def __init__(self, voices: tuple[str, ...] = ("v1", "v2")) -> None:
        self._voices = list(voices)
        self.calls: list[tuple[str, str]] = []

    def voices(self) -> list[str]:
        return list(self._voices)

    async def synthesize(self, text: str, voice: str) -> Synthesis:
        self.calls.append((text, voice))
        await asyncio.sleep(0.01)
        return Synthesis(b"mp3:" + text.encode("utf-8"), 2.0)
