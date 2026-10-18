---
name: playgama-publish
description: Take ANY HTML5 game (URL or ZIP), integrate the Playgama Bridge SDK, generate REAL gameplay cover assets, then create and fully fill the game form on the Playgama developer console (create, form, archive, covers, sandbox) — via MCP or direct REST API. Use when the user wants to "publish a game on Playgama", "submit a game to playgama", "fill the playgama game form", or "integrate the playgama bridge sdk".
---

# Playgama Bridge publishing — from a game URL to a filled, sandbox-ready game

This skill lets an AI agent take ANY HTML5 game given as a URL (direct zip URL,
portal page, or already-installed build directory) and:

1. **Download & unpack** the build.
2. **Integrate the Playgama Bridge SDK** correctly (mandatory to submit anything on Playgama).
3. **Capture REAL gameplay assets** (covers: square 800×800, portrait 1080×1920, landscape 1920×1080) of the build it just integrated — never AI-drawn art.
4. **Create the game and fill the form** on the Playgama developer console (`developer.playgama.com`), preferring the official **MCP server** and falling back to the **direct REST API** (both validated live).
5. Optionally **publish a sandbox** (public link, no moderation) so the developer can playtest immediately.

Submission to moderation is deliberately NOT automated — it is a human action
in the console. The agent fills everything and says "ready to submit".

---

## 0. Prerequisites

- Node.js + Playwright (`npm i playwright` + `npx playwright install chromium`; run HEADED under `xvfb-run -a` on Linux)
- Python 3 with `pillow` (`pip install pillow`)
- One of: a working **MCP connection** to `developer.playgama.com/api/mcp` (OAuth), or console account **email + password** (Supabase auth, direct REST).

## 1. Platform knowledge (validated live — read first!)

- **Official MCP server** (preferred transport): `POST https://developer.playgama.com/api/mcp` — stateless JSON-RPC (MCP protocol `2025-06-18`), name `playgama-developer-console`. Auth: **OAuth2 with PKCE** (`/api/oauth/register`, `/authorize`, `/token`), scope `mcp`, token type `pgm_oat_…`, `refresh_token` supported. No device-code flow — a browser "Allow" page is required (valid 10 min).
- **Direct REST fallback**: base `https://developer.playgama.com/api`. Session auth is Supabase: `POST https://sjytgtpfajawitsvmmdf.supabase.co/auth/v1/token?grant_type=password` with header `apikey: <anon key from developer.playgama.com HTML>` and body `{email,password}` → `access_token` (Bearer for every `/api` call). The anon key is printed in `window.initialEnv` of any console HTML page — fetch it at runtime, never hardcode it.
- **Validated REST endpoints** (all tested live):
  | Action | Endpoint |
  |---|---|
  | whoami / org | `GET /api/user` |
  | list my games | `GET /api/dashboard/stats?limit=25&skip=0&statuses=[]&userId=[]&sort=updatedAt&order=desc&search=` |
  | create game | `POST /api/applications/new` body `{"title":"…","engine":"construct3"}` → `201 {"id":…, "status":"DRAFT"}` |
  | delete game | `DELETE /api/applications/{id}` |
  | game detail | `GET /api/applications/{id}` |
  | archive info | `GET /api/archives/{archiveId}/info` |
- Writes the MCP owns exclusively: **archive upload, covers, form edits, sandbox, telemetry**. Even when using REST for create/list, prefer MCP for these or mirror the console's own calls (probe `static/js/*.js` of the console, the SPA fetch wrapper is `fetch('/api'+path, {Authorization: Bearer <supabase token>})`).
- **Build rules**: ZIP with `index.html` at the archive ROOT, ≤ 300 MB uncompressed, Bridge SDK **2.2.0+** integrated, working offline (index must not fail without network), no external analytics/tracking.
- **Cover exact sizes**: `square = 800×800`, `portrait = 1080×1920`, `landscape = 1920×1080`; PNG or JPEG; ≤ 10 MB; uploaded through the MCP cover 3-step flow (`start_cover_upload` → signed `PUT` with EXACT `Content-Length`/`Content-Type` signed headers → `confirm_cover_upload {applicationId, mediaId}`). 409 = nothing uploaded, 422 = wrong size/format (reason given, re-start), 502 = publish failed, safe to repeat.
- **Form content**: Title (2–4 words, matches in-game title), Engine, Description, How to play, Supported Devices, Orientation, Game Languages, Distribution, Link. Build check states: `CHECKING` → `PASSED` / `FAILED`; `bridgeSdk`: `FOUND`; archive `analysis.bridgeVersion` must be ≥ 2.2.0.
- **Name un-secretly**: never put developer/internal names on covers; covers must be pure gameplay frames (like GamePix QA discipline). No title text overlay.

## 2. Transport decision (do this FIRST, every run)

```
IF MCP tokens exist and refresh works  (pg_oauth tokens file, env PG_MCP_TOKENS, or ~/.playgama-tokens.json)
    → transport = mcp
ELSE IF PG_EMAIL + PG_PASSWORD provided
    → transport = rest  (login via Supabase, then REST endpoints above; use MCP-style scripts where the
      SPA's own bundle reveals the missing write endpoints — see references/api-map.md probing recipe)
ELSE
    → ASK the user for tokens or credentials. Continue in dry-run mode only with explicit consent.
```

Both transports are implemented in `scripts/playgama_release.py`:
- `--transport mcp` : JSON-RPC `tools/call` on `/api/mcp`, auto `initialize`+`notifications/initialized` handshake, token refresh on 401.
- `--transport rest` : Supabase login then the verified REST endpoints (also exposes `DELETE` cleanup helpers).

Headless MCP OAuth (no GUI needed):
1. `POST /api/oauth/register` with a `redirect_uris` entry of your choice (e.g. `http://localhost:1880/oauth/callback`) → `client_id`.
2. Generate PKCE (S256) + `state`, open `GET /api/oauth/authorize?...` in ANY browser (phone works) — user clicks "Allow".
3. Redirect lands on `localhost…?code=…` — paste the code back (or catch it on a local listener); exchange at `/api/oauth/token` with `code_verifier`. Store `{access_token, refresh_token}`.
Refresh: `grant_type=refresh_token`. Tokens persist; re-auth only when revoked.

## 3. End-to-end pipeline (the actual job)

### 3a. Acquire the build
- Direct zip URL → `curl -L -o build.zip`. Portal pages (Poki/GameDistribution/etc.) → find the iframe/embedded build URL from the page's `gameconfig`/`loader` JS, then zip it. Already-local dir → use as-is.
- Verify: `index.html` present; note engine (Unity/GL, Construct, Phaser, Godot export…) and orientation (portrait if `height > width` in viewport/canvas).

### 3b. Integrate the Bridge SDK (mandatory, before ANY upload)
Use `scripts/integrate_bridge.py`:
```bash
python3 scripts/integrate_bridge.py --dir ./build        # or --zip build.zip -o build-bridged.zip
```
It injects, as the FIRST `<head>` script:
```html
<script src="https://bridge.playgama.com/v2/stable/playgama-bridge.js"></script>
<script>window.bridgeConfig = {/* optional per-game config */};</script>
```
…then re-zips. Games calling `PokiSDK.*` or `GameSnacks.*` must ALSO get the matching bridge from `scripts/bridges/` (load FIRST, before the Bridge script): they map `commercialBreak → showInterstitial`, `rewardedBreak → showRewarded` (real rewards only on `rewarded` state), `score/level → platform.sendMessage`, and hold **audio** themselves (QA rule: no game audio during ads; pause on `visibilitychange`).

Required Bridge steps the build must get (checklist tool recomputes live: MCP `get_bridge_sdk_docs` / `get_game_checklist`):
- `game_ready` message once playable, `loading` progress, pause+mute on `pause_state_changed`/`audio_state_changed`, rewarded only on `rewarded` (never on `opened`).

### 3c. Assets = REAL gameplay captures (never AI-drawn)
```bash
xvfb-run -a node scripts/capture_assets.js --zip build-bridged.zip --out-dir ./assets --settle 8 --frames 40
```
- Launches THIS build under Playwright, auto-clicks through menus (`PLAY/START/tap-to-play`, language pickers), plays for `--settle` seconds, then captures a burst of frames while injecting input to stay in gameplay (no menus, no loading screens).
- `scripts/capture_postprocess.py` selects the most action-rich frames and produces **exactly**:
  - `cover_square_800x800.png` (also usable as icon slot), `cover_portrait_1080x1920.png`, `cover_landscape_1920x1080.png`
  - full-bleed square corners (rounded corners get re-cropped), corner pixel probe, LANCZOS, each ≤ 10 MB JPEG/PNG,
  - writes `ASSETS_SOURCE=assets_gameplay_capture` marker — the only provenance accepted by this skill before declaring the game "form-ready".

### 3d. Playgama form — create + fill (both transports)
```bash
python3 scripts/playgama_release.py \
  --transport mcp \
  --title "Coin Rush Dash" --engine construct3 \
  --description "..." --how-to-play "..." \
  --devices DESKTOP,IOS,ANDROID --languages EN \
  --orientation landscape --distribute-everywhere \
  --cover-dir ./assets \
  --archive build-bridged.zip \
  --sandbox
```
What it does, in order (idempotent; safe to re-run):
1. `list my games` → create if absent (`create_application` MCP / `POST /api/applications/new` REST — both return `applicationId`).
2. `update_application_form` (title/description/how-to-play/devices/languages/orientation/distribution/link/category from `get_game_categories`).
3. **Archive** (MCP, 3 steps): `start_archive_upload {applicationId, name, contentSize}` → signed S3 `PUT` (exact `Content-Length`!) → `confirm_archive_upload` → poll `get_archive_status` until not `CHECKING`; gate: refuses to continue if `state.status != PASSED` or `bridgeSdk != FOUND` or `analysis.bridgeVersion < 2.2.0`.
4. **Covers** (MCP, 3 steps each): `start_cover_upload {applicationId, cover: square|portrait|landscape, format, contentSize}` → signed `PUT` → `confirm_cover_upload`.
5. **Sandbox** (optional, `--sandbox`): `publish_sandbox` → sandbox link; **never auto-run `start_sandbox_traffic`** (ad campaign is irreversible) without explicit user consent.
6. Prints final status + QA tool link (`get_archive_qa_tool_link mode=certification` / local variant) and the checklist state.

REST fallback covers everything in step 1 and reads; for steps 3–5 on REST-only the
skill instructs the agent to mirror the console SPA's own network calls (see §1 probing
recipe) — MCP remains the supported path for those writes; prefer the MCP.

## 3e. Clean the build (MANDATORY — 0 in-game hits)

The build shipped to Playgama must be **fully cleaned of its origin**: neither
the source platform name (whatever it is: Poki, Softgames, Construct, Phaser,
PixiJS, Unity, Godot, Famobi, GameSnacks, …) nor the original game name may
appear anywhere inside the shipped build — code, assets, atlases, manifests,
metadata, strings, URLs, file names. A partial clean is a FAILED build.

- The repo/folder name is an **internal label** only and is not the game
  title; it does not prove cleanliness — scrub the build regardless.
- The original game name is **not** the repo name or the Pages URL for this
  workflow: the build simply carries **no name at all** inside it; the title
  is chosen fresh at publish time (2–4 words, matching the in-game title if
  any) and lives only in the Playgama dashboard and covers.
- Verify before declaring ready with the provided test:
  `node scripts/verify_clean.js <build-dir> <platform-name> <original-game-name>`
  (add any extra terms as further args). It exits 0 only when every term has
  0 hits in both file contents and file names; otherwise it prints the
  offending files/lines and exits 1. Run it in CI/pre-submit; a non-zero exit
  blocks submission.
- The npm script `npm run verify-clean -- <build-dir> <platform> <original-name>`
  wraps the same check.

## 4. Definition of "form ready to submit" (verify all before telling the user)

- [ ] Archive: `PASSED`, `bridgeSdk: FOUND`, bridge ≥ 2.2.0
- [ ] Covers: square + portrait + landscape confirmed (agent verifies via `get_application` → `media` list or 409/422 feedback)
- [ ] Form: title, engine, description, how-to-play, devices, languages, orientation, distribution, link — all set; check `get_submission_state`
- [ ] Sandbox (if requested): link returned (users play, stats flow)
- [ ] Tell the user: pressing "Submit Game" in the console is theirs — everything is ready.

Pre-QA agent checklist (before declaring ready): build opens standalone (`python3 -m http.server` + Playwright), Bridge initialized mock (or on-platform), all start buttons reachable, no `alert/confirm`, no external links outside the build, ads flow has pause+mute, rewarded gates on the actual `rewarded` state, cover crops look like the game currently running.

## 5. Common traps (validated live)

- S3 `PUT` signatures are exact-length: content byte size must match `contentSize` sent at `start_*` (use `wc -c`), and `Content-Length` must be sent. A mismatch = opaque storage refusal.
- MCP requires `Accept: application/json, text/event-stream` and a real `initialize` handshake per HTTP session (stateless server; you may open a new session per call).
- 401 from `/api/mcp` mid-flow = access token expired → issue refresh (`grant_type=refresh_token`), else re-run OAuth.
- `confirm_cover_upload` 422 names the exact mismatch (e.g. size not 800×800) — regenerate, never re-push the same file.
- Supabase `weak_password` flag is informational; `invalid_credentials` is not — stop and re-ask the user.
- Never put title/names on covers, never use menu/loading frames, never exercise `start_sandbox_traffic` without consent.
