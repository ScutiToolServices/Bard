# Bard: the admin web UI

With the service's admin password configured, open `http://127.0.0.1:29400/admin/` and sign in to manage status, model backends and settings. Public request authentication remains separate from that admin password.

## Navigation

The sidebar opens seven pages. The URL hash links directly to a page; browser Back and Forward work between them. Collapse the sidebar to keep an icon rail. Choose **System**, **Light** or **Dark** in the theme control. Theme and sidebar preferences are stored only in this browser; narrow screens keep the rail visible.

| Page | What it does |
|---|---|
| Overview (`#overview`) | Service-specific status and live `GET /health` results, including liveness and readiness |
| Playground (`#playground`) | Real public API requests with a caller token and requester username |
| Access tokens (`#tokens`) | Create and revoke caller tokens |
| Model connections (`#backends`) | Test and save model slots and named connections |
| Runtime & settings (`#settings`) | Edit saved options and reset them to their environment defaults |
| Usage (`#usage`) | Calls, errors and average time per requester since this process started |
| Connect & API (`#connect`) | Caller addresses, routes and Copy buttons |

## Model connections

Open **Model connections**, choose a saved connection or **Another address**, enter the reachable model-service address and token, then use **Test connection** and **Save**. Changes apply to the next model call. Credentials are saved in the state folder and are never returned by admin APIs. A slot without an address cannot run a model locally.

Named connections can be reused by slots. Editing one updates the slots that use it; a connection in use cannot be deleted. **Disconnect slot** clears its saved connection. These remote-only services need a connection before inference works.

Piper and Paradee slots call TTS_Piper_Model and TTS_Paradee_Model. Overview reports the default voice and available voice count. Bard holds no speech model.

Readiness means at least one model-slot address is configured. It does not prove every slot is connected or a model can complete a request.

## Runtime & settings and Usage

Saved settings override environment defaults. **Reset** removes an option's saved value. Options marked restart apply after the service restarts; the other options apply live. Usage is kept in memory and resets when the process restarts.

## Access tokens

**Access tokens** makes one token per caller (Armory, another service). Enter a name, optionally a calls-per-minute limit and an expiry in days, and press **Create token**. The token is shown once: copy it into the caller's configuration (for Armory, the extension's `X-Internal-Token` header). **Revoke** cuts a caller off at once. Tokens are stored as hashes in `tokens.json` beside `settings.json`. With no token, every call to `/mcp` and `/api/*` is refused; `/health` and `/admin` need none.

## Connect & API

**Connect & API** lists the addresses other services can use: the address you opened the page with, the same PC (`127.0.0.1`), another Docker container on the same network, and a Docker container calling this PC (`host.docker.internal`), then every endpoint with its method and a **Copy** button. Calls need a token from **Access tokens** in `X-Internal-Token`; HTTP routes also need `X-Requester-Username`.

## Try it: send a real request

1. Sign in to `/admin/` and open **Playground**.
2. Paste a service token into the token field. This is the public client token, not the admin password. Use a token made on **Access tokens** and supply a requester username; do not use a model-backend token.
3. Choose an operation, complete its labeled fields and send it. Requests go to this service's public route, with the same token scope, rate/admission limits and cache as a separate client.
4. Read the HTTP status, elapsed time and response. NDJSON/SSE text appears while it arrives; JSON is formatted. HTTP errors, including 401, 422, 429 and 503, keep their response body visible. Use **Cancel** to abort an active request.
5. Copy the curl equivalent to repeat the request elsewhere. The token is always a placeholder; fill it locally. For an upload, provide the indicated input file. Multi-step operations show their follow-up request too.

The pasted public token is held only in memory. The playground does not persist it in browser storage, put it in a URL, log it or include it in curl. Sign out or reset the panel to clear it. The existing admin sign-in is separate. A playground run consumes normal service quotas and can generate retained files where the public API normally does so; cancelling the browser fetch does not promise to undo work already accepted by a backend.

The browser caps uploads at **10 MiB**, displayed text at **256 KiB**, and binary audio/image responses at **32 MiB**. Server limits still apply and may be lower. Media uses bounded Blob URLs, revoked when replaced or cleared. Text is rendered as text, never executed as HTML. Audio players become available after the bounded audio download finishes. No new environment setting is needed.

### This service's request

Enter the requester username that will own the generated audio, choose an installed voice and enter text. `POST /api/speak` returns a file ID; the panel fetches `GET /api/files/{file_id}` with the same token and username and displays its MP3 with playback controls and a download. Voice choices come from `GET /api/voices`. This exercises the same text preparation, quotas, cache and engine selection as ordinary clients. Generated audio follows the normal retention policy.

The public token is sent only in `X-Internal-Token`. The requester username is sent in `X-Requester-Username` and determines ownership and quota. Admin requests continue to use `X-Admin-Password`. The shared tool page learns its service-specific playground descriptor from authenticated admin status metadata. Deploy the complete `web/` directory, including `playground.js`.
