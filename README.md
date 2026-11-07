# Bard

Text to speech for Ember. A TTSPiperModel or TTSParadeeModel service synthesizes the voice, ffmpeg encodes MP3, and Armory proxies the tools as the `bard` extension. Ember plays the result in chat through `GET /api/bard/audio/{file_id}`.

## Tools

| Tool | Purpose |
| --- | --- |
| `tool_bard_speak` | Turn text into an MP3 owned by the asking user. Returns `file_id`, `duration_s`, `size_bytes`, `cached`, `message`. |
| `tool_bard_listVoices` | List installed voices and the default. Read-only. Open to every user. |

## Engines

Bard runs no speech engine itself. Connect a `TTSPiperModel` and/or a `TTSParadeeModel` service on the admin page first; until one is connected the voice list is empty and speaking fails. Both sit behind one list: the `voice` of each request picks the service, and the list holds the Piper voices the token may use plus Paradee's four when its service is connected.

| | Piper | Paradee |
| --- | --- | --- |
| Voices | Any voice installed on the TTSPiperModel service. | Four fixed voices in one 8.5 MB model: `af_heart` (default), `bf_isabella`, `am_puck`, `bm_fable`. |
| Measured here | About 24x real time, 280 MB. | About 36x real time, 600 MB. |

`BARD_VOICE` is the default voice (a Piper voice unless you name a Paradee one). Voice names differ between the engines, so cached audio from one engine is never served for the other. 

## Setup

1. `uv sync` (`winget install astral-sh.uv` first if uv is missing)
2. Start a `TTSPiperModel` (and optionally `TTSParadeeModel`) service and make a token for Bard on its admin page; its projects list must include every voice Bard offers.
3. Copy `.env.example` to `.env` and set `INTERNAL_API_TOKEN` to the value Armory and ember_api use.
4. `run.bat run` (loads `.env`; `uv run python -m src.run --dotenv` does the same), then start Armory (it connects to Bard at startup, so Bard goes first).
5. Register it in Armory's `configs/config_extensions.json` (see `config_extensions.json.example`, entry `bard`) and set `bard_url` in ember_api's `configs/config_app.json` if Bard is not on `http://127.0.0.1:8090`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `INTERNAL_API_TOKEN` | empty | Shared token for `/mcp` and `/api/*`. Required when `BARD_HOST` is not loopback. |
| `BARD_HOST` / `BARD_PORT` | `127.0.0.1` / `8090` | Listen address. |
| `BARD_VOICE` | `en_US-lessac-medium` | Default voice. |
| `BARD_MAX_CHARS` | `5000` | Longest text accepted after Markdown is stripped. |
| `BARD_TTL_HOURS` | `24` | How long audio is kept. |
| `BARD_QUOTA_MB` | `200` | Audio storage per user. |
| `BARD_MAX_CONCURRENT` | `1` | Simultaneous syntheses. |
| `BARD_FFMPEG` | `ffmpeg` | ffmpeg executable. |

## Notes

- Audio is private to the asking user (their username from Armory's `forward_requester`) and expires after the time-to-live.
- Check the voice's licence before redistributing it; voices are downloaded, not committed.

## HTTP speech for Ember

`POST /api/speak` accepts JSON `{text, voice?}` and returns `{file_id, duration_s, voice}` through `BardService.speak`. Markdown cleanup, the 5,000 cleaned-character cap, owner cache, quota and synthesis semaphore all apply. `GET /api/voices` returns `{voices: string[]}` containing installed voices only. Both require `X-Requester-Username` and the configured internal token, just like `/api/files/{file_id}`. Invalid text/voice is 400, quota is 413 and synthesis failure is 503.

Ember proxies these routes with `tools.execute` and `files.download`. Its Read aloud action asks for a voice on every run, strips Markdown, batches at 3,500 characters and synthesizes one batch ahead during playback. Stop aborts pending browser requests and playback; already completed audio retains the existing owner and TTL rules. Agent-triggered audio cards and all MCP tools keep their existing behavior.

### Releasing

A plain publish (`run.bat`, Deploy, option 1) only updates the `live` branch, so it is fine for test deploys. A release is a separate step: set `VERSION` (and `pyproject.toml` where it has a version, to the same number), commit, then run `run.bat`, Deploy, option R or `py tools\publish_live.py --release`. That publishes to `live` and tags the `main` commit `vX.Y.Z` and the live commit `live-vX.Y.Z`. A version already released with different live files is refused, so bump `VERSION`. While a version is not officially released yet, add `--replace` to move its tags.

`run.bat`, Build the release ZIP, then builds `_zip_versions\Bard-<VERSION>.zip` (no `tools/`, no git-ignored files, no older ZIPs; `.env.example` is included) and opens GitHub's new-release page for the tag.

## Admin page

With `BARD_ADMIN_PASSWORD` set, `/admin/` (header `X-Admin-Password`) has four parts:

- **Status**: what the service runs and for how long, plus the default voice and how many voices are installed.
- **Usage**: calls, errors and average time per user since the service started (kept in memory, so a restart clears it).
- **Model services**: Piper and Paradee each run at a `TTSPiperModel` / `TTSParadeeModel` address (address and token are saved on the admin page only; Bard has no local engine). Pick a saved connection or **Another address**, set the address and token, press **Test connection** (it calls the other service with that token), then **Save**. A change applies to the next call; the token is stored in `.data/backends.json`, never shown again, and `Use the environment value` drops the saved one (back to not connected).
- **Settings**: the options saved in `.data/settings.json`.

## Try it from the admin page

Open `/admin/` and use **Try it** with the service's internal API token and a requester username. The token is held only in memory, separate from admin sign-in. Requests use the ordinary public API, quotas and cache; the panel shows HTTP status, latency, output, cancellation and curl with a token placeholder. See [the web UI guide](docs/guide-web-ui.md).

Enter the requester username that will own the generated audio, choose an installed voice and enter text. `POST /api/speak` returns a file ID; the panel fetches `GET /api/files/{file_id}` with the same token and username and displays its MP3 with playback controls and a download. Voice choices come from `GET /api/voices`. This exercises the same text preparation, quotas, cache and engine selection as ordinary clients. Generated audio follows the normal retention policy.

## Documentation

- [Purpose: input, output and what it is for](docs/purpose.md)
- [Deployment](deployment.md)
- [Changelog](CHANGELOG.md)
- [Docker on this PC](docs/guide-docker-same-device.md)
- [Docker on another machine](docs/guide-docker-deployment-device.md)
- [Run without Docker](docs/guide-no-docker-same-device.md)
