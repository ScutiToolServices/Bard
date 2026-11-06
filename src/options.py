"""The Bard options the admin page can change. The default shown is the value at start (.env or built in), which
load_settings already read; a saved value wins over it."""

from __future__ import annotations

from src.config import Settings
from src.runtime import Config, LiveSettings, Setting

MB = 1024 * 1024

# attribute of Settings -> (setting key, conversion to the attribute's unit)
FIELDS = {
    "max_chars": ("max_chars", int),
    "ttl_seconds": ("ttl_hours", lambda hours: hours * 3600),
    "quota_bytes": ("quota_mb", lambda mb: mb * MB),
    "voice": ("voice", str),
    "max_concurrent": ("max_concurrent", int),
    "ffmpeg": ("ffmpeg", str),
}


def schema(base: Settings) -> list[Setting]:
    return [
        Setting("max_chars", "Longest text per request (characters)", "int", base.max_chars, minimum=1, maximum=100000),
        Setting("ttl_hours", "Keep audio for (hours)", "int", base.ttl_seconds // 3600, minimum=1, maximum=8760),
        Setting("quota_mb", "Audio storage per user (MB)", "int", base.quota_bytes // MB, minimum=1, maximum=100000),
        Setting("voice", "Default voice", "text", base.voice, blank=False,
                help="An installed voice."),
        Setting("max_concurrent", "Speech jobs at once", "int", base.max_concurrent, minimum=1, maximum=16,
                restart=True),
        Setting("ffmpeg", "ffmpeg program", "text", base.ffmpeg, blank=False, help="A name on PATH or a full path"),
        Setting("data_path", "Data folder", "text", "", restart=True,
                help=f"Where stored data (audio and voices) goes. Empty = {base.state_dir}. Applies after a restart; files already stored are not moved. "
                     f"Settings and saved model connections stay in the standard folder."),
    ]


def make_config(base: Settings) -> Config | None:
    """None, so nothing is changeable at run time, unless BARD_ADMIN_PASSWORD is set."""
    return Config(base.state_dir / "settings.json", schema(base)) if base.admin_password else None


def live(base: Settings, config: Config | None):
    return LiveSettings(base, config, FIELDS) if config else base
