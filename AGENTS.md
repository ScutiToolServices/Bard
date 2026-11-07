# Bard

Text-to-speech MCP server for Citadel and Ember. A standalone service on port 29400, registered in Armory as the `bard` extension. Design: `Python/Citadel/docs/superpowers/specs/2026-10-25-bard-design.md`.

## Agent instructions
This file is the one instruction file for every coding agent; `CLAUDE.md` only imports it (`@AGENTS.md`). Edit this file, never `CLAUDE.md`.

## Commands (from this folder, venv `.venv`, managed by uv)
- Install: `uv sync` (`winget install astral-sh.uv` if missing; on this machine set `UV_CACHE_DIR=D:/User/.uv-cache`)
- Test: `uv run pytest -q`
- Run: `run.bat run`, or `uv run python -m src.run` (add `--dotenv` to load `.env`)

## Rules
- Follow the `tool-service-parity` skill and `AI/ToolServices/_docs/SHARED-FEATURES.md` (what must match in every tool service) and `AI/ToolServices/_docs/SERVICE-SPECIFIC.md` (what differs on purpose). A change to a shared feature goes to every tool service; a new difference goes into `SERVICE-SPECIFIC.md` in the same change.
- Never read or print `.env`.
- Data folder: `Settings.data_dir` holds audio and voices; the admin setting `data_path` (restart, `src/config.py` `with_saved_data_path`) moves it at the next start and keeps `settings.json` and `backends.json` in `Settings.state_dir` (the first folder). No `.env` variable; `SERVICE_DATA_DIR` is read only as the base folder (Docker).
- Logic lives in `service.py`, `engine.py`, `store.py`; `app.py` is glue. Tools never call an AI.
- `build_engine` returns a `MultiEngine` of two `SlotEngine`s (Piper and Paradee). Bard runs no speech engine itself: each slot is a `TTS_Piper_Model` / `TTS_Paradee_Model` address saved on the admin page, and with none saved calls fail with a hint to connect one. The request's `voice` picks the slot (Paradee's four names, else Piper). The default voice is a Piper voice.
- Every blocking call (the voice list fetch, file IO) runs through `asyncio.to_thread`; ffmpeg runs as an asyncio subprocess.
- `INTERNAL_API_TOKEN` guards `/mcp` and `/api/*`; an exposed bind without it must refuse to start.
- Result models end with `message`. Error messages never contain paths.
- The owner key is the username from `_meta.requester`.
- Bard has no tool to download, list downloadable or delete voices; the voices listed are the ones the connected model services allow the token.
- Runtime settings: with `SERVICE_ADMIN_PASSWORD` set, `/admin/` (page in `web/`, API `/admin/settings`, header `X-Admin-Password`) edits the options in `options.py`; they are saved to `.data/settings.json` and win over `.env`. `src/runtime.py` is shared with Scribe and copies Laya's `settings.py`: keep the copies in step. Live options are read through `LiveSettings`; `restart=True` ones (`max_concurrent`) apply at the next start. Without the password the page is off and nothing is changeable. `INTERNAL_API_TOKEN` does not guard `/admin`.
- Model location: with an address saved for the `piper` / `paradee` slot on the admin page, that engine is `HttpTtsEngine` (in `src/engine.py`): `POST /v1/speak` to the `TTS_Piper_Model` / `TTS_Paradee_Model` service with the slot's saved token as `X-Internal-Token`; `GET /v1/voices` (kept 30 s) feeds `voices()`. ffmpeg (WAV to MP3) stays in Bard. No address = not connected: the voice list is empty and speaking fails. A service token's projects list must include every voice Bard offers. Remote failures become `EngineFailed` / `VoiceUnavailable` without the URL or the service's text.
- Admin page (`web/index.html`, `src/admin_api.py`, `tests/test_admin_api.py`): the same three files in Bard, Scribe and Reader; change one, copy it to the other two. With `SERVICE_ADMIN_PASSWORD` set, `/admin/` shows status and usage per user (`GET /admin/status`, in-memory `Usage`, reset on restart), the model-service slots (`/admin/backends`: where Piper and Paradee (`piper` and `paradee` slots, `SlotEngine` in `src/engine.py`) runs, address + token saved in `.data/backends.json`, never sent back, read on every call) and the settings. Header `X-Admin-Password`. Each service passes its own slots, `status` items and probes to `register(...)` in `app.py`. Speak calls are counted through `usage.track` in `tool_bard_speak` and `/api/speak`.
- Saved connections: the admin page also keeps a shared list of named model-service addresses (`/admin/connections/{name}`, under the reserved `_connections` key of `backends.json`, token never sent back). A slot picks one with `PUT /admin/backends/{key}` `{"connection": name}`, so editing a connection updates every slot using it; a connection a slot uses cannot be deleted (409). Addresses and tokens are saved only on the admin page (there are no environment variables for them); a slot with no address runs in this service, or fails when it has no local engine.
- Behaviour changes update `README.md`, `deployment.md`, the guides in `docs/`, `.env.example`, `AGENTS.md` and `CHANGELOG.md` in the same commit.
- Commit with the noreply author email `214214011+scutiii2@users.noreply.github.com` (another address is rejected on push). Push `main`, then run `python tools/publish_live.py`. Build Docker only when the Dockerfile or the dependencies changed.
