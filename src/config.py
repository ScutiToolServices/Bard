"""Bard settings, read from the environment (and a local .env, never committed)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
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
    config_dir: Path | None = None  # set when the data folder was moved: the folder that keeps settings.json
    voice: str = "en_US-lessac-medium"
    ffmpeg: str = "ffmpeg"
    max_chars: int = 5000
    ttl_seconds: int = 24 * 3600
    quota_bytes: int = 200 * 1024 * 1024
    max_concurrent: int = 1
    # Unlocks /admin/ (settings page). Empty = the page is off and settings stay as the environment set them.
    admin_password: str = ""

    @property
    def state_dir(self) -> Path:
        """Where settings.json and backends.json live: the first data folder, even after the data folder was moved."""
        return self.config_dir or self.data_dir

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def exposed_without_token(self) -> bool:
        """Listening beyond loopback with no token lets anything that can reach the port call every tool."""
        return not self.internal_token and self.host not in LOOPBACK_HOSTS


PARADEE_VOICES = ("af_heart", "bf_isabella", "am_puck", "bm_fable")  # the four voices of Paradee 1.2
DEFAULT_VOICE = "en_US-lessac-medium"  # a Piper voice; the voice of each request picks Piper or Paradee


def _from_environment() -> Settings:
    load_dotenv(PROJECT_DIR / ".env")
    env = os.environ.get
    voice = env("BARD_VOICE") or DEFAULT_VOICE
    return Settings(
        host=env("BARD_HOST", "127.0.0.1"),
        port=int(env("BARD_PORT", "8090")),
        internal_token=env("INTERNAL_API_TOKEN", ""),
        data_dir=Path(env("BARD_DATA_DIR") or PROJECT_DIR / ".data"),
        voice=voice,
        ffmpeg=env("BARD_FFMPEG", "ffmpeg"),
        max_chars=int(env("BARD_MAX_CHARS", "5000")),
        ttl_seconds=int(env("BARD_TTL_HOURS", "24")) * 3600,
        quota_bytes=int(env("BARD_QUOTA_MB", "200")) * 1024 * 1024,
        max_concurrent=int(env("BARD_MAX_CONCURRENT", "1")),
        admin_password=env("BARD_ADMIN_PASSWORD", ""),
    )


def with_saved_data_path(settings: Settings) -> Settings:
    """The admin page's "Data folder" setting moves stored data (audio, uploads, voices, results) to another folder.
    It is read once, here at start. Settings and saved model connections stay in the first folder, which is where this
    value is saved. A folder that cannot be written is ignored."""
    try:
        saved = json.loads((settings.data_dir / "settings.json").read_text("utf-8")).get("data_path")
    except (OSError, ValueError, AttributeError):
        return settings
    if not isinstance(saved, str) or not saved.strip():
        return settings
    target = Path(saved.strip()).expanduser()
    if not target.is_absolute():
        target = PROJECT_DIR / target
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-test"
        probe.write_text("x")
        probe.unlink()
    except OSError:
        logging.getLogger(__name__).warning("Data folder %s cannot be written; using %s", target, settings.data_dir)
        return settings
    return replace(settings, data_dir=target, config_dir=settings.data_dir)


def load_settings() -> Settings:
    return with_saved_data_path(_from_environment())
