# Bard

Text-to-speech MCP server for Citadel and Ember. A standalone service on port 8090, registered in Armory as the `bard` extension. Design: `Python/Citadel/docs/superpowers/specs/2026-10-25-bard-design.md`.

## Agent instructions
This file is the one instruction file for every coding agent; `CLAUDE.md` only imports it (`@AGENTS.md`). Edit this file, never `CLAUDE.md`.

## Commands (from this folder, venv `.venv`, managed by uv)
- Install: `uv sync` (`python -m pip install uv` if missing; on this machine set `UV_CACHE_DIR=D:/User/.uv-cache`)
- Voice: `uv run python -m piper.download_voices en_US-lessac-medium --download-dir .data/voices`
- Test: `uv run pytest -q`
- Run: `uv run python -m src.run`

## Rules
- Never read or print `.env`.
- Logic lives in `service.py`, `engine.py`, `store.py`; `app.py` is glue. Tools never call an AI.
- `build_engine` returns a `MultiEngine`: Piper always, Paradee too when the optional `paradee` extra is installed (imported only when its first text is spoken). The request's `voice` picks the engine. `BARD_ENGINE` only picks the default voice's engine. Paradee's voices are fixed, so download and delete of them refuse.
- Every blocking call (Piper, Paradee, file IO) runs through `asyncio.to_thread`; ffmpeg runs as an asyncio subprocess.
- `INTERNAL_API_TOKEN` guards `/mcp` and `/api/*`; an exposed bind without it must refuse to start.
- Result models end with `message`. Error messages never contain paths.
- The owner key is the username from `_meta.requester`.
- Admin-only tools (`tool_bard_listDownloadable`, `tool_bard_downloadVoice`) go through `AdminGate`, which asks ember_api for `bard.admin` and fails closed. Voice names must come from the catalog; installs are staged and checked before they reach `.data/voices/`.
- `tool_bard_deleteVoice` needs its own permission, `bard.delete`, which `bard.admin` does not imply. It uses a second `AdminGate(permission="bard.delete")` with no static admins (`BARD_ADMINS` never grants delete). The default voice cannot be deleted.
