# Playgama developer console — endpoint map (probes validated live, Oct 2026)

## Transports

- **MCP** (preferred): stateless JSON-RPC over HTTP POST `https://developer.playgama.com/api/mcp`,
  protocol `2025-06-18`, server `playgama-developer-console` (v1.7.0 seen live; 35 tools).
  Requires `Accept: application/json, text/event-stream`. Unauthenticated calls return
  `401` with `WWW-Authenticate: Bearer …, scope="mcp"`.
  OAuth discovery (RFC 9728 / 8414): `/.well-known/oauth-protected-resource` →
  `authorization_servers: ["https://developer.playgama.com"]`; server metadata at
  `/.well-known/oauth-authorization-server` → authorize `/api/oauth/authorize`,
  token `/api/oauth/token`, dynamic registration `/api/oauth/register`, revoke
  `/api/oauth/revoke`; `code_challenge_methods_supported:["S256"]`,
  `grant_types:["authorization_code","refresh_token"]`. No device-code grant.
- **REST fallback**: base `https://developer.playgama.com/api`, Bearer = Supabase
  access token. Login: `POST https://sjytgtpfajawitsvmmdf.supabase.co/auth/v1/token?grant_type=password`
  with `apikey` header = anon key (fetched from `window.initialEnv` inside any console
  HTML page — discover at runtime, never hardcode).
  Errors: `invalid_credentials` = wrong email/password pair (final), plus
  `weak_password` informational flag on success.

## Verified REST endpoints (probes live)

| Action | Call | Result |
|---|---|---|
| whoami | `GET /api/user` | `{"id","supabaseId","email","name","organizationRole":"OWNER","organization":{"studioName"}}` |
| list games | `GET /api/dashboard/stats?limit=25&skip=0&statuses=%5B%5D&userId=%5B%5D&sort=updatedAt&order=desc&search=` | `{"list":[{id,title,status,inSandbox,…}],"total":N}` |
| create game | `POST /api/applications/new` body `{"title":"…","engine":"construct3"}` | 201 `{"id",…,"status":"DRAFT"}` (accepts optional `agent`,`modelId`) |
| game detail | `GET /api/applications/{id}` | full form incl. `media[]` tags `icon`,`preview`,`preview_vertical` |
| delete draft | `DELETE /api/applications/{id}` | 200 |
| archive info | `GET /api/archives/{archiveId}/info` | `{"archive":{"id","name","url"},"archiveData":{"bridgeEngine","bridgeVersion","initialSize","loadingTime"},"application":{…}}` |
| QA headless path | console route `/check-game-headless/:archiveId` (SPA route) | QA tool rendered by the console |

Statuses seen live: `DRAFT`, `WAITING` (moderation queue w/ `applicationTasks[].taskId:"MOD-####"`,
`status`, `comment`), `APPROVED` (live). Moderation communication = `list_moderation_comments`.

## MCP tool surface (35 tools, live)

Reads: `list_applications`, `get_application {applicationId}`, `get_submission_state {applicationId, archiveId}`,
`list_moderation_comments`, `get_archive_qa_tool_link {applicationId, archiveId, mode:"certification"}`
(→ `{"url","processing","failedAt","bridgeSdk"}`), `get_sandbox_state`, `get_publications`,
`get_launch_steps {applicationId}` (steps: archive→bridge→covers→form→sandbox→share→traffic),
`get_game_checklist [page]`, `get_game_categories`, `get_bridge_sdk_docs`.

Writes: `create_application {title, engine}`; `update_application_form {applicationId, title…}`;
`start_archive_upload {applicationId, name, contentSize}` → `{"archiveId","uploadUrl","method":"PUT","headers"}`
→ S3 `PUT` (signed for exact `contentSize`; send `Content-Length`!) → `confirm_archive_upload`
→ poll `get_archive_status` (`state: PUSHED?` no — `CHECKING` → `PASSED|FAILED`, `processing: DONE`,
`bridgeSdk: FOUND`, `analysis.bridgeVersion` ≥ 2.2.0 required);
`start_cover_upload {applicationId, cover: square|portrait|landscape, format: png|jpeg, contentSize}`
(sizes: 800×800 / 1080×1920 / 1920×1080; ≤ 10 MiB) → signed `PUT` → `confirm_cover_upload {applicationId, mediaId}`
(409 nothing uploaded, 422 size/format mismatch — reason given, start new, 502 republish, safe to repeat);
`publish_sandbox {applicationId}`; `start_sandbox_traffic` (irreversible ad campaign — human call);
in-app catalog and leaderboards tools when needed.

Deliberately withheld (human-only in console): submit to moderation, rollback,
delete/attach screenshots outside cover slots, payouts, delete leaderboards, delete games.

## Probing recipe (when REST writes are missing)

The console SPA fetch wrapper (`function B(e,t)` in `static/js/index-*.js` + chunks like
`NewGame-*.js`, `Applications-*.js`, `Archive-*.js`) builds every call as
`fetch('/api'+path, {Authorization: Bearer <supabase token>})`. That is how
`POST /applications/new` was found (in `NewGame-*.js`). Process:

1. `curl -s https://developer.playgama.com/<page> -o p.html`
2. `grep -oE 'static/js/[A-Za-z0-9_.-]+\.js'` → fetch the relevant chunk
3. `grep -oE '\(B\("[^"]+"' chunk.js` and `grep -oE 'method:"[a-z]+"'` → endpoint + verb
4. Mimic with the Bearer header; unknown emit NestNS 404 `Cannot … /path` which maps 1:1 to `/api` + path.

## Upload signature gotchas

- Both archive and cover `start_*` tools sign the S3 PUT URL for the EXACT byte size you
  send (`contentSize`); refuse/refresh with a mismatched size, and `Content-Length`
  must be transmitted (they are in `SignedHeaders`).
- Cover `confirm_cover_upload` validates actual bytes: wrong format (png vs jpeg) or wrong
  dimensions → 422 with the reason; start a NEW upload to retry.
- Archive check states: `PENDING` → `CHECKING` → `PASSED|FAILED` (poll ~6 s; ~1–3 min typical).
