"""Result models. `message` comes last: ember shows it first."""

from __future__ import annotations

from pydantic import BaseModel


class SpeakResult(BaseModel):
    file_id: str
    voice: str
    duration_s: float
    size_bytes: int
    cached: bool
    message: str


class VoiceList(BaseModel):
    voices: list[str]
    default: str
    message: str
