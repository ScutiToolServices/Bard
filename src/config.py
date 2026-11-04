"""Bard settings, read from the environment (and a local .env, never committed)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8090
    internal_token: str = ""
    data_dir: Path = PROJECT_DIR / ".data"
    engine: str = "piper"  # "piper" or "paradee"
    voice: str = "en_US-lessac-medium"
    ffmpeg: str = "ffmpeg"
    # Empty = run Piper / Paradee in this process. Set = call that model service (AI/ModelServices) with its token.
    piper_url: str = ""
    piper_token: str = ""
    paradee_url: str = ""
    paradee_token: str = ""
    max_chars: int = 5000
    ttl_seconds: int = 24 * 3600
    quota_bytes: int = 200 * 1024 * 1024
    max_concurrent: int = 1
    # Unlocks /admin/ (settings page). Empty = the page is off and settings stay as the environment set them.
    admin_password: str = ""

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def exposed_without_token(self) -> bool:
        """Listening beyond loopback with no token lets anything that can reach the port call every tool."""
        return not self.internal_token and self.host not in LOOPBACK_HOSTS


ENGINES = ("piper", "paradee")
PARADEE_VOICES = ("af_heart", "bf_isabella", "am_puck", "bm_fable")  # the four voices of Paradee 1.2
DEFAULT_VOICES = {"piper": "en_US-lessac-medium", "paradee": "af_heart"}


def load_settings() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")
    env = os.environ.get
    engine = env("BARD_ENGINE", "piper").strip().lower()
    if engine not in ENGINES:
        raise ValueError(f"BARD_ENGINE must be one of {', '.join(ENGINES)}, not {engine!r}")
    voice = env("BARD_VOICE") or DEFAULT_VOICES[engine]
    if engine == "paradee" and voice not in PARADEE_VOICES:
        raise ValueError(f"BARD_VOICE {voice!r} is not a Paradee voice; use one of {', '.join(PARADEE_VOICES)}")
    return Settings(
        host=env("BARD_HOST", "127.0.0.1"),
        port=int(env("BARD_PORT", "8090")),
        internal_token=env("INTERNAL_API_TOKEN", ""),
        data_dir=Path(env("BARD_DATA_DIR") or PROJECT_DIR / ".data"),
        engine=engine,
        voice=voice,
        ffmpeg=env("BARD_FFMPEG", "ffmpeg"),
        piper_url=env("BARD_PIPER_URL", ""),
        piper_token=env("BARD_PIPER_TOKEN", ""),
        paradee_url=env("BARD_PARADEE_URL", ""),
        paradee_token=env("BARD_PARADEE_TOKEN", ""),
        max_chars=int(env("BARD_MAX_CHARS", "5000")),
        ttl_seconds=int(env("BARD_TTL_HOURS", "24")) * 3600,
        quota_bytes=int(env("BARD_QUOTA_MB", "200")) * 1024 * 1024,
        max_concurrent=int(env("BARD_MAX_CONCURRENT", "1")),
        admin_password=env("BARD_ADMIN_PASSWORD", ""),
    )
