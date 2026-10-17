#!/usr/bin/env python3
"""
playgama_release.py — Playgama developer-console client (MCP-first, REST fallback).

Validated live against developer.playgama.com (Oct 2026):

  MCP transport
    POST https://developer.playgama.com/api/mcp          (stateless JSON-RPC, MCP 2025-06-18)
    OAuth: /api/oauth/register | /authorize | /token     (PKCE S256, scope=mcp)

  REST transport
    Supabase auth: POST https://sjytgtpfajawitsvmmdf.supabase.co/auth/v1/token?grant_type=password
      -> {access_token, refresh_token, expires_at, ...}
    then Bearer token on https://developer.playgama.com/api/... :
      GET  /user                 whoami / org
      GET  /dashboard/stats      list my games (query: limit,skip,statuses,userId,sort,order,search)
      POST /applications/new     {title, engine} -> 201 {id, status:DRAFT, ...}
      GET  /applications/{id}    game detail
      DELETE /applications/{id}  delete draft
      GET  /archives/{id}/info   archive info / analysis

Commands:
  auth-mcp [--manual]           full OAuth (local callback listener; --manual prints URL to click & paste code from phone)
  refresh                       refresh stored tokens
  whoami                        account + organization
  list                          my games
  create --title T --engine E   create draft (returns id)
  delete --id ID                delete draft
  form --id ID [--title ... --description ... --how-to-play ... --languages EN --devices DESKTOP,IOS,ANDROID ...]  (MCP update_application_form)
  upload-archive --id ID --zip F  3-step archive upload + poll + gate on PASSED/bridge>=2.2.0
  upload-covers --id ID --cover-dir D  square/portrait/landscape 3-step uploads
  sandbox --id ID               publish sandbox (public link)
  qa --id ID --archive A        QA tool link

Env:
  PG_TOKENS_FILE   default ./playgama_tokens.json (MCP access/refresh tokens)
  PG_EMAIL / PG_PASSWORD  for REST fallback
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import subprocess  # noqa: F401  (available for future HTTP needs)
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

MCP_URL = "https://developer.playgama.com/api/mcp"
BASE = "https://developer.playgama.com"
SUPABASE_URL = "https://sjytgtpfajawitsvmmdf.supabase.co"
SUPABASE_ANON_FALLBACK = None  # discovered at runtime from console HTML
MIN_BRIDGE = (2, 2, 0)

COVER_SPECS = {
    "square": (800, 800),
    "portrait": (1080, 1920),
    "landscape": (1920, 1080),
}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def log(*a: Any) -> None:
    print("[PG]", *a, file=sys.stderr)


def http_json(url: str, method: str = "GET", body: Any = None, headers: Optional[dict] = None,
              timeout: int = 60) -> tuple[int, Any, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read().decode("utf-8", "replace")
        return r.status, raw, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


# ---------------------------------------------------------------- tokens file
def tokens_path() -> str:
    return os.environ.get("PG_TOKENS_FILE", os.path.join(os.getcwd(), "playgama_tokens.json"))


def load_tokens() -> Dict[str, Any]:
    p = tokens_path()
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def save_tokens(t: Dict[str, Any]) -> None:
    with open(tokens_path(), "w") as f:
        json.dump(t, f, indent=1)
    try:
        os.chmod(tokens_path(), 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- MCP transport
class Mcp:
    def __init__(self, access: str, refresh: Optional[str] = None):
        self.access = access
        self.refresh = refresh

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.access}",
            "User-Agent": UA,
        }

    def _rpc(self, payload: Dict[str, Any], expect_reply: bool = True, _retry: bool = True) -> Any:
        st, raw, _ = http_json(MCP_URL, "POST", payload, self._headers())
        if st == 401 and _retry and self.refresh:
            log("401 from MCP — trying refresh_token")
            if self.refresh_tokens():
                return self._rpc(payload, expect_reply, _retry=False)
            raise SystemExit("MCP 401 and refresh failed — run: %s auth-mcp" % (sys.argv[0],))
        if st != 200:
            raise RuntimeError(f"MCP HTTP {st}: {raw[:300]}")
        if not expect_reply or not raw.strip():
            return None
        if "data:" in raw and raw.strip().startswith(("event:", "data:")):
            for line in raw.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            return None
        return json.loads(raw)

    def refresh_tokens(self) -> bool:
        if not self.refresh:
            return False
        st, raw, _ = http_json(f"{BASE}/api/oauth/token", "POST",
                               urllib.parse.urlencode({
                                   "grant_type": "refresh_token",
                                   "refresh_token": self.refresh,
                                   "client_id": load_tokens().get("client_id", ""),
                               }).encode(),
                               {"Content-Type": "application/x-www-form-urlencoded"})
        st, body = (st, raw)
        try:
            tok = json.loads(body)
        except Exception:
            tok = {}
        if st < 300 and "access_token" in tok:
            cur = load_tokens()
            cur.update({k: tok[k] for k in ("access_token", "refresh_token") if k in tok})
            cur.pop("access_token", 0)
            save_tokens(cur)
            self.access = tok["access_token"]
            self.refresh = tok.get("refresh_token", self.refresh)
            log("token refreshed OK")
            return True
        log("refresh failed:", st, body[:200])
        return False

    def start(self) -> None:
        self._rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "playgama-publish-skill", "version": "1.0"}}}, expect_reply=False)
        try:
            self._rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_reply=False)
        except Exception:
            pass

    def call(self, name: str, args: Dict[str, Any]) -> str:
        res = self._rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": name, "arguments": args}})
        if isinstance(res, dict) and "error" in res:
            raise RuntimeError(json.dumps(res["error"])[:400])
        txt = ""
        for c in (res or {}).get("result", {}).get("content", []):
            if c.get("type") == "text":
                txt += c["text"]
        return txt


def mcp_from_tokens() -> Mcp:
    t = load_tokens()
    if not t.get("access_token"):
        raise SystemExit("no MCP tokens — run auth-mcp first (or use --transport rest)")
    m = Mcp(t["access_token"], t.get("refresh_token"))
    m.start()
    return m


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class _Callback(http.server.BaseHTTPRequestHandler):
    code = None

    def do_GET(self):  # noqa: N802
        q = urllib.parse.urlparse(self.path)
        if q.path.startswith("/oauth/callback"):
            self.__class__.code = urllib.parse.parse_qs(q.query).get("code", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK - return to your agent")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):  # silence
        pass


def auth_mcp(manual: bool, port: int = 1880) -> None:
    # 1) dynamic client registration
    redirect = f"http://localhost:{port}/oauth/callback"
    st, raw, _ = http_json(f"{BASE}/api/oauth/register", "POST", {
        "client_name": "playgama-publish-skill",
        "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "mcp",
    }, {"Content-Type": "application/json"})
    if st >= 300:
        raise SystemExit(f"register failed {st}: {raw[:300]}")
    client = json.loads(raw)
    # 2) PKCE + state
    verifier = _b64u(secrets.token_bytes(32))
    challenge = _b64u(hashlib.sha256(verifier.encode()).digest())
    state = secrets.token_urlsafe(16)
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": client["client_id"],
        "redirect_uri": redirect, "scope": "mcp", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    auth_url = f"{BASE}/api/oauth/authorize?{q}"
    log("OAUTH URL (open it in any browser, incl. your phone):")
    print(auth_url, flush=True)

    code = None
    if manual:
        raw = input("paste the redirect URL you were redirected to (…?code=…&iss=…): ").strip()
        code = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query).get("code", [None])[0]
    else:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), _Callback)
        log(f"waiting for callback on :{port} …")
        t0 = time.time()
        while _Callback.code is None and time.time() - t0 < 600:
            srv.handle_request()
        srv.server_close()
        code = _Callback.code
    if not code:
        raise SystemExit("no authorization code received")
    # 3) exchange (PKCE proves ownership; this is the headless-safe leg)
    st, raw, _ = http_json(f"{BASE}/api/oauth/token", "POST",
                           urllib.parse.urlencode({
                               "grant_type": "authorization_code", "code": code,
                               "client_id": client["client_id"], "redirect_uri": redirect,
                               "code_verifier": verifier,
                           }).encode(),
                           {"Content-Type": "application/x-www-form-urlencoded"})
    tok = json.loads(raw)
    if st >= 300 or "access_token" not in tok:
        raise SystemExit(f"token exchange failed {st}: {raw[:300]}")
    save_tokens({"client_id": client["client_id"],
                 "access_token": tok["access_token"],
                 "refresh_token": tok.get("refresh_token"),
                 "scope": tok.get("scope", "mcp")})
    log("tokens saved to", tokens_path())


# ---------------------------------------------------------------- REST transport
def supabase_login(email: str, password: str) -> Dict[str, Any]:
    anon = os.environ.get("PG_SUPABASE_ANON_KEY") or SUPABASE_ANON_FALLBACK
    if not anon:
        st, raw, _ = http_json(f"{BASE}/", headers={"User-Agent": UA})
        i = raw.find("SUPABASE_ANON_KEY")
        if i >= 0:
            j = raw.find('"', i)
            j2 = raw.find('"', j + 1)
            anon = raw[j + 1:j2]
    if not anon:
        raise SystemExit("could not discover Supabase anon key; set PG_SUPABASE_ANON_KEY")
    st, raw, _ = http_json(f"{SUPABASE_URL}/auth/v1/token?grant_type=password", "POST",
                           {"email": email, "password": password},
                           {"apikey": anon, "Content-Type": "application/json"})
    try:
        tok = json.loads(raw)
    except Exception:
        tok = {}
    if st >= 300 or "access_token" not in tok:
        raise SystemExit(f"supabase login failed {st}: {raw[:200]}")
    tok["supabase_anon"] = anon
    return tok


def api(headers_extra: Optional[dict] = None, method: str = "GET", path: str = "",
        body: Any = None, token: Optional[str] = None, timeout: int = 60) -> tuple[int, Any]:
    h = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if headers_extra:
        h.update(headers_extra)
    st, raw, _hdr = http_json(BASE + "/api" + path, method, body, h, timeout)
    try:
        return st, json.loads(raw)
    except Exception:
        return st, raw


def rest_upload(url: str, data: bytes, content_type: str) -> int:
    req = urllib.request.Request(url, data=data, method="PUT",
                                 headers={"Content-Type": content_type,
                                          "Content-Length": str(len(data)),
                                          "User-Agent": UA})
    r = urllib.request.urlopen(req, timeout=300)
    return r.status


# ---------------------------------------------------------------- gates
def gate_archive(mcp: Optional[Mcp], status_txt: str) -> Dict[str, Any]:
    try:
        st = json.loads(status_txt)
    except Exception:
        raise RuntimeError("bad archive status: " + status_txt[:200])
    state = (st.get("state") or {}).get("status")
    bridge = st.get("bridgeSdk")
    ver = (st.get("analysis") or {}).get("bridgeVersion") or "0"
    try:
        v = tuple(int(x) for x in ver.split(".")[:3])
    except Exception:
        v = (0, 0, 0)
    ok = state == "PASSED" and bridge == "FOUND" and v >= MIN_BRIDGE
    if not ok:
        raise RuntimeError(f"build gate FAILED: state={state} bridge={bridge} version={ver} raw={status_txt[:300]}")
    log("build gate PASSED (bridge", ver, ")")
    return st


# ---------------------------------------------------------------- command impls
def cmd_create(rest_token: Optional[str], mcp: Optional[Mcp], a) -> None:
    if mcp:
        out = mcp.call("create_application", {"title": a.title, "engine": a.engine})
        print(out)
        return
    st, j = api(method="POST", path="/applications/new",
                body={"title": a.title, "engine": a.engine}, token=rest_token)
    if st >= 300:
        raise SystemExit(f"create failed {st}: {json.dumps(j)[:300]}")
    print(json.dumps({"id": j["id"], "status": j.get("status")}, indent=1))
    log("created", j["id"])


def cmd_list(rest_token: Optional[str], mcp: Optional[Mcp]) -> None:
    if mcp:
        print(mcp.call("list_applications", {}))
        return
    st, j = api(method="GET", path="/dashboard/stats?limit=25&skip=0&statuses=%5B%5D&userId=%5B%5D&sort=updatedAt&order=desc&search=",
                token=rest_token)
    print(json.dumps(j, indent=1) if st < 300 else f"list failed {st}: {json.dumps(j)[:300]}")


def cmd_whoami(rest_token: Optional[str]) -> None:
    st, j = api(method="GET", path="/user", token=rest_token)
    keys = {k: j.get(k) for k in ("email", "name", "role", "organizationRole")}
    if isinstance(j, dict) and j.get("organization"):
        keys["organization"] = j["organization"].get("studioName")
    print(json.dumps(keys if st < 300 else {"error": st, "raw": json.dumps(j)[:200]}, indent=1))


def cmd_form(mcp: Mcp, a) -> None:
    args: Dict[str, Any] = {"applicationId": a.id}
    for k_mcp, flag in [("title", "title"), ("description", "description"), ("howToPlayText", "how_to_play")]:
        v = getattr(a, flag)
        if v:
            args[k_mcp] = v
    if a.languages:
        args["supportedLanguages"] = [x.strip() for x in a.languages.split(",") if x.strip()]
    if a.devices:
        args["supportedDevices"] = [x.strip() for x in a.devices.split(",") if x.strip()]
    if a.orientation:
        args["orientation"] = a.orientation if a.orientation != "landscape" else "landscape"
    if a.category:
        args["category"] = a.category
    print(mcp.call("update_application_form", args))


def cmd_upload_archive(mcp: Mcp, a) -> None:
    name = os.path.basename(a.zip)
    size = os.path.getsize(a.zip)
    data = open(a.zip, "rb").read()
    st_txt = mcp.call("start_archive_upload", {"applicationId": a.id, "name": name, "contentSize": size})
    info = json.loads(st_txt)
    log("archiveId", info["archiveId"], "size", size)
    code = rest_upload(info["uploadUrl"], data, info.get("headers", {}).get("Content-Type", "application/zip"))
    if code != 200:
        raise SystemExit(f"archive PUT failed {code}")
    log("archive PUT OK; confirming…")
    mcp.call("confirm_archive_upload", {"applicationId": a.id, "archiveId": info["archiveId"]})
    for i in range(40):
        s = mcp.call("get_archive_status", {"applicationId": a.id, "archiveId": info["archiveId"]})
        try:
            cur = (json.loads(s).get("state") or {}).get("status")
        except Exception:
            cur = "?"
        log("poll", i, cur)
        if cur and cur != "CHECKING":
            gate_archive(mcp, s)
            print(s)
            return
        time.sleep(6)
    raise SystemExit("timeout waiting for build check")


def cmd_upload_covers(mcp: Mcp, a) -> None:
    which = [c for c in (a.covers or "square,portrait,landscape").split(",") if c]
    for slot in which:
        kind = "square" if slot.startswith("square") else slot
        fname = {
            "square": ["cover_square_800x800.png", "cover_square_800x800.jpg", "square.png", "square.jpg"],
            "portrait": ["cover_portrait_1080x1920.png", "cover_portrait_1080x1920.jpg", "portrait.png", "portrait.jpg"],
            "landscape": ["cover_landscape_1920x1080.png", "cover_landscape_1920x1080.jpg", "landscape.png", "landscape.jpg"],
        }[kind]
        path_img = next((os.path.join(a.cover_dir, f) for f in fname if os.path.exists(os.path.join(a.cover_dir, f))), None)
        if not path_img:
            log("missing", kind, "cover in", a.cover_dir, "— skipped")
            continue
        img = open(path_img, "rb").read()
        is_png = path_img.endswith(".png") or img[:8] == b"\x89PNG\r\n\x1a\n"
        fmt = "png" if is_png else "jpeg"
        st_txt = mcp.call("start_cover_upload", {"applicationId": a.id, "cover": kind, "format": fmt, "contentSize": len(img)})
        info = json.loads(st_txt)
        code = rest_upload(info["uploadUrl"], img, info.get("headers", {}).get("Content-Type", f"image/{fmt}"))
        if code != 200:
            raise SystemExit(f"cover {kind} PUT failed {code}")
        conf = mcp.call("confirm_cover_upload", {"applicationId": a.id, "mediaId": info["mediaId"]})
        log(kind, "confirmed:", conf[:200])


def cmd_sandbox(mcp: Mcp, a) -> None:
    print(mcp.call("publish_sandbox", {"applicationId": a.id}))


def cmd_qa(mcp: Mcp, a) -> None:
    print(mcp.call("get_archive_qa_tool_link",
                   {"applicationId": a.id, "archiveId": a.archive, "mode": "certification"}))


# ---------------------------------------------------------------- main
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--transport", choices=["mcp", "rest"], default="mcp")
    p.add_argument("--email", default=os.environ.get("PG_EMAIL"))
    p.add_argument("--password", default=os.environ.get("PG_PASSWORD"))
    sub = p.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("auth-mcp"); pm.add_argument("--manual", action="store_true"); pm.add_argument("--port", type=int, default=1880)
    sub.add_parser("refresh")
    sub.add_parser("whoami")
    sub.add_parser("list")
    pc = sub.add_parser("create"); pc.add_argument("--title", required=True); pc.add_argument("--engine", default="construct3")
    pd = sub.add_parser("delete"); pd.add_argument("--id", required=True)
    pf = sub.add_parser("form"); pf.add_argument("--id", required=True)
    for fl, k in [("--title", "title"), ("--description", "description"), ("--how-to-play", "how_to_play"),
                  ("--languages", "languages"), ("--devices", "devices"), ("--orientation", "orientation"),
                  ("--category", "category"), ("--link", "link")]:
        pf.add_argument(fl, dest=k, default=None)
    pu = sub.add_parser("upload-archive"); pu.add_argument("--id", required=True); pu.add_argument("--zip", required=True)
    pv = sub.add_parser("upload-covers"); pv.add_argument("--id", required=True); pv.add_argument("--cover-dir", required=True); pv.add_argument("--covers", default="square,portrait,landscape")
    ps = sub.add_parser("sandbox"); ps.add_argument("--id", required=True)
    pq = sub.add_parser("qa"); pq.add_argument("--id", required=True); pq.add_argument("--archive", required=True)

    a = p.parse_args()

    mcp: Optional[Mcp] = None
    rest_token: Optional[str] = None
    mcp_only = a.cmd in ("form", "upload-archive", "upload-covers", "sandbox", "qa")

    if mcp_only or a.transport == "mcp":
        try:
            mcp = mcp_from_tokens()
        except SystemExit:
            if mcp_only:
                raise
            mcp = None
    if a.transport == "rest" or (a.cmd in ("create", "list", "delete", "whoami") and not mcp):
        if not (a.email and a.password):
            tok = supabase_login(a.email, a.password) if a.email else None
        else:
            tok = supabase_login(a.email, a.password)
        rest_token = tok["access_token"] if tok else None

    if a.cmd == "auth-mcp":
        auth_mcp(a.manual, a.port); return
    if a.cmd == "refresh":
        mcp_from_tokens().refresh_tokens(); return
    if a.cmd == "whoami":
        cmd_whoami(rest_token); return
    if a.cmd == "list":
        cmd_list(rest_token, mcp); return
    if a.cmd == "create":
        cmd_create(rest_token, mcp, a); return
    if a.cmd == "delete":
        rest_token = rest_token or _rt_slack()
        st, _ = api(method="DELETE", path=f"/applications/{a.id}", token=rest_token)
        print("delete:", st)
        return
    if mcp is None:
        raise SystemExit("this command requires the MCP transport — run auth-mcp first")
    if a.cmd == "form":
        cmd_form(mcp, a); return
    if a.cmd == "upload-archive":
        cmd_upload_archive(mcp, a); return
    if a.cmd == "upload-covers":
        cmd_upload_covers(mcp, a); return
    if a.cmd == "sandbox":
        cmd_sandbox(mcp, a); return
    if a.cmd == "qa":
        cmd_qa(mcp, a); return


def _rt_slack() -> Optional[str]:
    """REST token straight from env (used by the delete helper)."""
    tok = os.environ.get("PG_REST_TOKEN")
    return tok


if __name__ == "__main__":
    main()
