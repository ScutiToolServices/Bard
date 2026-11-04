# Bard

Text-to-speech MCP server for Citadel and Ember. A standalone service on port 8090, registered in Armory as the `bard` extension. Design: `Python/Citadel/docs/superpowers/specs/2026-10-25-bard-design.md`.

## Agent instructions
This file is the one instruction file for every coding agent; `CLAUDE.md` only imports it (`@AGENTS.md`). Edit this file, never `CLAUDE.md`.

## Commands (from this folder, venv `.venv`, managed by uv)
- Install: `uv sync` (`winget install astral-sh.uv` if missing; on this machine set `UV_CACHE_DIR=D:/User/.uv-cache`)
- Voice: `uv run python -m piper.download_voices en_US-lessac-medium --download-dir .data/voices`
- Test: `uv run pytest -q`
- Run: `uv run python -m src.run`

## Rules
- Never read or print `.env`.
- Logic lives in `service.py`, `engine.py`, `store.py`; `app.py` is glue. Tools never call an AI.
- `build_engine` returns a `MultiEngine`: Piper always, Paradee too when the optional `paradee` extra is installed (imported only when its first text is spoken). The request's `voice` picks the engine. With `BARD_ENGINE=paradee` and the extra installed it returns `ParadeeEngine` alone, so only Paradee's voices are listed; otherwise `BARD_ENGINE` only picks the default voice's engine.
- Every blocking call (Piper, Paradee, file IO) runs through `asyncio.to_thread`; ffmpeg runs as an asyncio subprocess.
- `INTERNAL_API_TOKEN` guards `/mcp` and `/api/*`; an exposed bind without it must refuse to start.
- Result models end with `message`. Error messages never contain paths.
- The owner key is the username from `_meta.requester`.
- Bard has no tool to download, list downloadable or delete voices; installed voices are listed only. Install voices on the host with `piper.download_voices`.
- Runtime settings: with `BARD_ADMIN_PASSWORD` set, `/admin/` (page in `web/`, API `/admin/settings`, header `X-Admin-Password`) edits the options in `options.py`; they are saved to `.data/settings.json` and win over `.env`. `src/runtime.py` is shared with Scribe and copies Laya's `settings.py`: keep the copies in step. Live options are read through `LiveSettings`; `restart=True` ones (`max_concurrent`) apply at the next start. Without the password the page is off and nothing is changeable. `INTERNAL_API_TOKEN` does not guard `/admin`.
- Model location: with `BARD_PIPER_URL` / `BARD_PARADEE_URL` set, that engine is `HttpTtsEngine` (in `src/engine.py`): `POST /v1/speak` to the `TTSPiperModel` / `TTSParadeeModel` service with `BARD_*_TOKEN` as `X-Internal-Token`; `GET /v1/voices` (kept 30 s) feeds `voices()`. ffmpeg (WAV to MP3) stays in Bard. Empty = the library in this process. A service token's projects list must include every voice Bard offers. Remote failures become `EngineFailed` / `VoiceUnavailable` without the URL or the service's text.
