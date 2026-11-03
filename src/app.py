"""The Bard server: two MCP tools, an audio file route and a health check."""

from __future__ import annotations

import asyncio
import hmac
import os
from pathlib import Path
from typing import Annotated, Literal

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from src.admin_gate import AdminGate
from src.config import PROJECT_DIR, Settings
from src.contract import DeleteResult, DownloadableVoices, DownloadResult, SpeakResult, VoiceList
from src.engine import TtsEngine, build_engine
from src.errors import BardError, EngineFailed, IdentityRequired, QuotaExceeded
from src.identity import owner_from_context
from src.middleware import InternalTokenMiddleware
from src.options import live, make_config
from src.runtime import Config
from src.service import AdminCheck, BardService, VoiceSource
from src.store import AudioStore
from src.voices import VoiceLibrary

INSTRUCTIONS = (
    "Text to speech. Call tool_bard_speak with the text to say; the chat shows a player and a download link. "
    "Pass only text a user should hear. Call tool_bard_listVoices to see the installed voices. "
    "tool_bard_listDownloadable and tool_bard_downloadVoice add new voices and are for Bard admins only. "
    "tool_bard_deleteVoice removes one and needs the separate bard.delete permission."
)


WEB_DIR = Path(os.environ.get("BARD_WEB_DIR") or PROJECT_DIR / "web")


class SpeakIn(BaseModel):
    """Strict HTTP input; the service cleans and bounds spoken text."""
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str
    voice: str | None = None


def build_app(
    settings: Settings,
    engine: TtsEngine | None = None,
    gate: AdminCheck | None = None,
    library: VoiceSource | None = None,
    delete_gate: AdminCheck | None = None,
    config: Config | None = None,
) -> Starlette:
    """`config` (default: from BARD_ADMIN_PASSWORD, see options.make_config) makes the options in options.py
    changeable on /admin/ while running; without it they stay as the environment set them."""
    config = config if config is not None else make_config(settings)
    base, settings = settings, live(settings, config)
    engine = engine or build_engine(base)
    store = AudioStore(base.audio_dir, lambda: settings.ttl_seconds, lambda: settings.quota_bytes)
    store.sweep()
    # shortcut: these HTTP clients live as long as the process and are not closed on shutdown.
    gate = gate or AdminGate(httpx.AsyncClient(), settings.ember_api_url, settings.internal_token, settings.admins)
    if library is None:
        library = VoiceLibrary(httpx.AsyncClient(follow_redirects=True), settings.voices_dir, settings.staging_dir)
    # No static admins here: BARD_ADMINS grants installing, never deleting.
    delete_gate = delete_gate or AdminGate(
        httpx.AsyncClient(), settings.ember_api_url, settings.internal_token, permission="bard.delete"
    )
    service = BardService(engine, store, settings, library, gate, delete_gate)
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
            return await service.speak(owner_from_context(ctx), text, voice)
        except BardError as error:
            raise ToolError(str(error)) from error

    @mcp.tool(
        meta={"keywords": ["voices", "tts"], "display_label": "Listing voices"},
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    async def tool_bard_listVoices() -> VoiceList:
        """List the installed voices and the default one. Read-only."""
        return await service.list_voices()

    @mcp.tool(
        meta={"keywords": ["voices", "download", "catalog", "tts", "admin"], "display_label": "Listing downloadable voices"},
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    )
    async def tool_bard_listDownloadable(
        language: Annotated[str | None, Field(description="Keep only this language: a family like 'en' or a code like 'en_GB'. Omit for all (about 180 voices).")] = None,
        quality: Annotated[Literal["x_low", "low", "medium", "high"] | None, Field(description="Keep only this quality tier. Omit for all.")] = None,
        ctx: Context = None,
    ) -> DownloadableVoices:
        """Bard admins only. List the voices that can be downloaded onto the Bard machine, with language, quality,
        size and whether each is already installed. Pass a language to keep the list short."""
        try:
            return await service.list_downloadable(owner_from_context(ctx), language, quality)
        except BardError as error:
            raise ToolError(str(error)) from error

    @mcp.tool(
        meta={"keywords": ["voices", "download", "install", "tts", "admin"], "display_label": "Downloading a voice"},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
    )
    async def tool_bard_downloadVoice(
        voice: Annotated[str, Field(description="A voice name from tool_bard_listDownloadable, for example en_US-ryan-high.")],
        ctx: Context = None,
    ) -> DownloadResult:
        """Bard admins only. Download one voice onto the Bard machine (tens of megabytes) so it can be used with
        tool_bard_speak. Refuses a voice that is already installed. Ask the user before downloading."""
        try:
            return await service.download_voice(owner_from_context(ctx), voice)
        except BardError as error:
            raise ToolError(str(error)) from error

    @mcp.tool(
        meta={"keywords": ["voices", "delete", "remove", "uninstall", "tts"], "display_label": "Deleting a voice"},
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False),
    )
    async def tool_bard_deleteVoice(
        voice: Annotated[str, Field(description="An installed voice name from tool_bard_listVoices.")],
        ctx: Context = None,
    ) -> DeleteResult:
        """Needs the bard.delete permission. Delete one installed voice from the Bard machine. The default voice
        cannot be deleted. Ask the user to confirm before deleting."""
        try:
            return await service.delete_voice(owner_from_context(ctx), voice)
        except BardError as error:
            raise ToolError(str(error)) from error

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"ok": True})

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

    if config is not None:
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
