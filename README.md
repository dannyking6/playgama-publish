# playgama-publish

AI-agent skill to take **any HTML5 game** (URL or ZIP) and go to a filled, sandbox-ready
Playgama game: **integrate the Playgama Bridge SDK** → **capture REAL gameplay assets**
(square 800×800 / portrait 1080×1920 / landscape 1920×1080) → **create the game and fill
the form** on `developer.playgama.com` — all through the official **MCP server**
(preferred) or the validated **direct REST API** fallback, 100% headless-friendly
(works over SSH without GUI; the OAuth "Allow" click can be done from a phone).

Sister skill of [`gamepix-publish`](https://github.com/dannyking6/gamepix-publish) — same
discipline (real gameplay captures only, SDK verified before flags, never ship a build you
can't prove is compliant), different platform.

## Transport decision (the skill does this first)

| Situation | Transport |
|---|---|
| MCP tokens file exists / refresh works | **MCP** (all write flows supported) |
| Only email + password | **REST** (create/list/whoami/delete verified live; write flows via MCP strongly recommended) |
| Neither | dry-run + ask the user |

## Install

```bash
npm i playwright && npx playwright install chromium
pip install pillow            # for the cover post-process
```

## Use as an agent skill

Point your agent at `SKILL.md`, e.g.:

```bash
npx skills add dannyking6/playgama-publish --skill playgama-publish --yes
```

## One-run example (everything from a game zip URL)

```bash
# 1) get the build
curl -LO https://example costing.com/mygame/build.zip && mv build.zip mygame.zip

# 2) integrate the mandatory Playgama Bridge SDK (first <head> script) + rezip
python3 scripts/integrate_bridge.py --zip mygame.zip -o mygame-bridged.zip

# 3) REAL gameplay captures of THIS build -> 3 exact cover specs, full-bleed
xvfb-run -a node scripts/capture_assets.js --zip mygame-bridged.zip --out-dir ./assets

# 4) MCP auth (headless: paste the ?code= redirect from any browser, phone OK)
python3 scripts/playgama_release.py auth-mcp           # ...then:
#   open the printed URL, Allow, paste back the localhost redirect URL

# 5) create + fill + archive + covers + sandbox
python3 scripts/playgama_release.py upload-archive --id <applicationId> --zip mygame-bridged.zip
python3 scripts/playgama_release.py upload-covers  --id <applicationId> --cover-dir ./assets
python3 scripts/playgama_release.py form --id <applicationId> \
  --description "…100-500 original chars…" --how-to-play "Desktop… Mobile…" \
  --languages EN --devices DESKTOP,IOS,ANDROID --orientation landscape
python3 scripts/playgama_release.py sandbox --id <applicationId>
```

REST fallback (no MCP tokens):

```bash
PG_EMAIL=you@example.com PG_PASSWORD='secret' \
python3 scripts/playgama_release.py --transport rest list
PG_EMAIL=you@example.com PG_PASSWORD='secret' \
python3 scripts/playgama_release.py --transport rest create --title "Coin Rush Dash" --engine construct3
```

Validated REST endpoints (tested live Oct 2026 — see `references/api-map.md`):
`POST /api/applications/new`, `GET /api/dashboard/stats`, `GET /api/user`,
`GET /api/applications/{id}`, `DELETE /api/applications/{id}`, `GET /api/archives/{id}/info`.

## Files

- `SKILL.md` — the real skill: transport decision, platform traps, full pipeline, gates
- `scripts/playgama_release.py` — MCP JSON-RPC client (auto-reinitialize + refresh) AND REST client; archive 3-step upload w/ exact-size gate; covers 3-step; sandbox; QA link
- `scripts/integrate_bridge.py` — injects `bridge.playgama.com/v2/stable/playgama-bridge.js` as FIRST `<head>` script (+ optional bridgeConfig), re-zips, verifies first-script position, scans for absolute URLs
- `scripts/capture_assets.js` — launches the REAL build, auto-plays past menus, burst-captures gameplay frames (needs playwright + xvfb)
- `scripts/capture_postprocess.py` — picks the richest frame, full-bleed crops to the exact platform sizes, corner probe, ≤10 MB, writes `ASSETS_SOURCE=assets_gameplay_capture`
- `scripts/bridges/poki_bridge.js` — PokiSDK games get REAL Playgama ads (interstitial/rewarded on the actual `rewarded` state) + audio-safe shim
- `scripts/bridges/snacks_bridge.js` — GameSnacks contract (beforeAd→beforeReward→showAdFn→adViewed→afterAd→adBreakDone) mapped onto Bridge rewarded + same audio-safe shim

## What the gates enforce (why your game won't bounce in QA)

1. **Archive gate**: refuses success message unless `state=PASSED`, `bridgeSdk=FOUND`, bridge ≥ 2.2.0.
2. **Assets provenance**: only `assets_gameplay_capture` output is counted "form-ready" (no AI/PIL placeholders — same rule that rejected 3 GamePix games).
3. **Never optional**: `game_ready` lifecycle, pause+mute during ads, rewarded granted ONLY on the true `rewarded` state.
4. **Moderation submit stays human**: MCP deliberately omits it; the skill fills everything and hands over.

## Disclaimer

Use with accounts you own. Playgama QA reviews every submission; games must respect
the self-check + Bridge requirements (relative paths, no external analytics, pause/mute
during ads, 300 MB build cap, index.html at zip root).
