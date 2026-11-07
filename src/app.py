"""The Bard server: two MCP tools, an audio file route and a health check."""

from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from src.admin_api import Slot, Usage, register
from src.config import PROJECT_DIR, Settings
from src.contract import SpeakResult, VoiceList
from src.engine import TtsEngine, build_engine, make_backends, probe_voices
from src.errors import BardError, EngineFailed, IdentityRequired, QuotaExceeded
from src.identity import owner_from_context
from src.middleware import InternalTokenMiddleware
from src.options import live, make_config
from src.runtime import Config
from src.service import BardService
from src.store import AudioStore

INSTRUCTIONS = (
    "Text to speech. Call tool_bard_speak with the text to say; the chat shows a player and a download link. "
    "Pass only text a user should hear. Call tool_bard_listVoices to see the installed voices."
)


WEB_DIR = Path(os.environ.get("BARD_WEB_DIR") or PROJECT_DIR / "web")

# Public client requests retain the ordinary internal-token and owner checks.
PLAYGROUND = {"operations": [{
    "label": "Speak", "route": "/api/speak", "method": "POST", "encoding": "json", "response": "json",
    "fields": [
        {"key": "X-Requester-Username", "label": "Requester username", "type": "text", "location": "header",
         "required": True, "default": "playground"},
        {"key": "text", "label": "Text", "type": "textarea", "required": True, "default": "Hello from Bard."},
        {"key": "voice", "label": "Voice", "type": "select", "options": []},
    ],
    "lookup": {"route": "/api/voices", "key": "voices", "field": "voice"},
    "followup": {"route": "/api/files/{file_id}", "method": "GET", "response": "audio"},
}]}


class SpeakIn(BaseModel):
    """Strict HTTP input; the service cleans and bounds spoken text."""
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str
    voice: str | None = None


CONNECT = {
    "host": "bard",
    "port": 8090,
    "paths": [
        {
            "label": "MCP, for Armory",
            "method": "POST",
            "path": "/mcp"
        },
        {
            "label": "Speak",
            "method": "POST",
            "path": "/api/speak"
        },
        {
            "label": "Voices",
            "method": "GET",
            "path": "/api/voices"
        },
        {
            "label": "Audio file",
            "method": "GET",
            "path": "/api/files/{file_id}"
        },
        {
            "label": "Health, no token",
            "method": "GET",
            "path": "/health"
        }
    ]
}


def build_app(
    settings: Settings,
    engine: TtsEngine | None = None,
    config: Config | None = None,
) -> Starlette:
    """`config` (default: from BARD_ADMIN_PASSWORD, see options.make_config) makes the options in options.py
    changeable on /admin/ while running; without it they stay as the environment set them."""
    config = config if config is not None else make_config(settings)
    base, settings = settings, live(settings, config)
    backends = make_backends(base)
    usage = Usage()
    engine = engine or build_engine(base, backends)
    store = AudioStore(base.audio_dir, lambda: settings.ttl_seconds, lambda: settings.quota_bytes)
    store.sweep()
    # shortcut: these HTTP clients live as long as the process and are not closed on shutdown.
    service = BardService(engine, store, settings)
    mcp = FastMCP(name="bard", instructions=INSTRUCTIONS, host=settings.host, port=settings.port)

    @mcp.tool(meta={"keywords": ["speak", "say", "read aloud", "tts", "voice", "audio"], "display_label": "Generating speech"})
    async def tool_bard_speak(
        text: Annotated[str, Field(description="The text to speak. Markdown is stripped; code blocks are skipped.")],
        voice: Annotated[str | None, Field(description="An installed voice name. Omit it for the default voice.")] = None,
        ctx: Context = None,
    ) -> SpeakResult:
        """Turn text into spoken English (MP3). Creates an audio file owned by the asking user; the chat shows
        a player and a download link, so do not paste a link yourself. Returns the file id, duration and a summary."""
        try:
            owner = owner_from_context(ctx)
            async with usage.track(owner, "speak"):
                return await service.speak(owner, text, voice)
        except BardError as error:
            raise ToolError(str(error)) from error

    @mcp.tool(
        meta={"keywords": ["voices", "tts"], "display_label": "Listing voices"},
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def tool_bard_listVoices() -> VoiceList:
        """List the installed voices and the default one. Read-only."""
        return await service.list_voices()

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True, "ready": bool(backends.get("piper") or backends.get("paradee"))})

    @mcp.custom_route("/api/files/{file_id}", methods=["GET"])
    async def audio(request: Request) -> Response:
        owner = request.headers.get("x-requester-username", "").strip()
        if not owner:
            return JSONResponse({"error": "identity_required"}, status_code=401)
        file_id = request.path_params["file_id"]
        stored = await asyncio.to_thread(store.get, owner, file_id)
        if stored is None:
            return JSONResponse({"error": "file_not_found"}, status_code=404)
        return FileResponse(
            stored.path,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f'inline; filename="bard-{file_id[:8]}.mp3"',
            },
        )

    @mcp.custom_route("/api/speak", methods=["POST"])
    async def speak(request: Request) -> Response:
        """Generate owned audio using the existing text preparation and cache."""
        owner = request.headers.get("x-requester-username", "").strip()
        if not owner:
            return JSONResponse({"error": "identity_required"}, status_code=401)
        try:
            body = SpeakIn.model_validate(await request.json())
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid_request", "message": "Send text and an optional installed voice."}, status_code=400)
        try:
            async with usage.track(owner, "speak"):
                result = await service.speak(owner, body.text, body.voice)
        except BardError as error:
            code = 401 if isinstance(error, IdentityRequired) else 413 if isinstance(error, QuotaExceeded) else 503 if isinstance(error, EngineFailed) else 400
            return JSONResponse({"error": error.code, "message": str(error)}, status_code=code)
        return JSONResponse({"file_id": result.file_id, "duration_s": result.duration_s, "voice": result.voice})

    @mcp.custom_route("/api/voices", methods=["GET"])
    async def voices(request: Request) -> Response:
        """Only installed voices; clients choose a voice explicitly on each run."""
        if not request.headers.get("x-requester-username", "").strip():
            return JSONResponse({"error": "identity_required"}, status_code=401)
        result = await service.list_voices()
        return JSONResponse({"voices": result.voices})

    async def status() -> list[dict[str, str]]:
        try:
            voices = f"{len(await asyncio.to_thread(engine.voices))} installed"
        except BardError:
            voices = "unavailable (a model service cannot be reached)"
        modes = [("Piper", "piper"), ("Paradee", "paradee")]
        where = [{"label": label, "value": "model service" if backends.get(key) else "not connected"} for label, key in modes]
        return [{"label": "Default voice", "value": settings.voice}, {"label": "Voices", "value": voices}, *where]

    if config is not None:
        register(mcp, connect=CONNECT, service="Bard", admin_password=base.admin_password, usage=usage, backends=backends, status=status,
                 playground=PLAYGROUND, slots=[
            Slot("piper", "Piper", "Text to speech with Piper: the address of a TTS_Piper_Model service. Its token's projects list must include every voice Bard offers.",
                 probe_voices, "http://127.0.0.1:9150", local=False),
            Slot("paradee", "Paradee", "Text to speech with Paradee's four voices: the address of a TTS_Paradee_Model service.",
                 probe_voices, "http://127.0.0.1:9160", local=False),
        ])

        @mcp.custom_route("/admin/settings", methods=["GET", "PUT"])
        async def admin_settings(request: Request) -> Response:
            given = request.headers.get("x-admin-password", "").encode("utf-8")
            if not hmac.compare_digest(given, base.admin_password.encode("utf-8")):
                return JSONResponse({"detail": "Wrong admin password."}, status_code=401)
            if request.method == "PUT":
                try:
                    values = (await request.json())["values"]
                    if not isinstance(values, dict):
                        raise TypeError
                except (ValueError, KeyError, TypeError):
                    return JSONResponse({"detail": "Send {\"values\": {...}}."}, status_code=422)
                try:
                    voice = values.get("voice")
                    if isinstance(voice, str) and voice.strip() not in await asyncio.to_thread(engine.voices):
                        raise ValueError(f"Voice '{voice.strip()}' is not installed.")
                    await asyncio.to_thread(config.set, values)
                except ValueError as error:
                    return JSONResponse({"detail": str(error)}, status_code=422)
            return JSONResponse({"service": "Bard", "settings": config.describe()})

    app = mcp.streamable_http_app()
    if config is not None and WEB_DIR.is_dir():
        app.router.routes.append(Mount("/admin", StaticFiles(directory=WEB_DIR, html=True)))
    app.add_middleware(InternalTokenMiddleware, token=settings.internal_token)
    return app
