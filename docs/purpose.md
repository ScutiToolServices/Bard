# Purpose

**Kind:** text → MP3 file

**Group:** Text-to-Speech

**Tags:** `cpu`, `mcp`, `http`, `ffmpeg`

| | |
|---|---|
| Input | Text (Markdown is stripped, up to 5000 cleaned characters) and an optional voice, from an MCP tool call or `POST /api/speak`. The asking user comes from `_meta.requester` or `X-Requester-Username`. |
| Output | An MP3 owned by that user: `file_id`, `duration_s`, `size_bytes`, `cached`. `GET /api/files/{file_id}` downloads it. The voice list is the voices the connected model services allow. |
| Needs | `TTS_Piper_Model` and/or `TTS_Paradee_Model`, one connection each on the admin page. With none connected the voice list is empty and speaking fails. |
| Runs on | CPU. Holds no model; ffmpeg encodes WAV to MP3. |

**Use for:** letting an Ember or Citadel agent read text aloud.
**Not for:** voice cloning (see `Mimic`), speech recognition (see `Scribe`).
