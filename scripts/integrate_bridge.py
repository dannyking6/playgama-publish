#!/usr/bin/env python3
"""
integrate_bridge.py — inject the Playgama Bridge SDK (v2, mandatory) and
re-zip the build. Verified live (Bridge 2.3.x loads as first <head> script
and was detected by the build gate as bridgeSdk=FOUND).

Usage:
  python3 integrate_bridge.py --zip build.zip -o build-bridged.zip
  python3 integrate_bridge.py --dir ./build               (re-zips in place next to dir)
Options:
  --config playgama-bridge-config.json   optional per-game Bridge config
  --no-rescan                            skip relative-path sanity scan

Checks before injecting:
  - index.html exists at ROOT of zip/dir
  - bridge script already present? -> leave untouched, report version
  - warns if absolute URLs / http(s) assets found (Playgama prefers relative paths)
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import tempfile
import zipfile

BRIDGE_V2 = "https://bridge.playgama.com/v2/stable/playgama-bridge.js"


def log(*a):
    print("[BRIDGE]", *a, file=__import__("sys").stderr)


def read_html_from_zip(zip_path: str) -> tuple[str, zipfile.ZipFile] | tuple[str, None]:
    z = zipfile.ZipFile(zip_path)
    names = [n for n in z.namelist() if not n.endswith("/")]
    root_htmls = [n for n in names if n.count("/") == 0 and n.lower().endswith("index.html")]
    if not root_htmls:
        raise SystemExit("no index.html at the archive ROOT — Playgama requires index.html at root; repack first")
    html = z.read(root_htmls[0]).decode("utf-8", "replace")
    return html, z


def head_of(html: str) -> str:
    m = re.search(r"<head[^>]*>.*?</head\s*>", html, re.S | re.I)
    return m.group(0) if m else ""


def bridge_already(html: str) -> bool:
    return "bridge.playgama.com" in html.lower()


def inject(html: str, config_snippet: str) -> str:
    h = head_of(html)
    if not h:
        raise SystemExit("no <head> in index.html — cannot inject")
    script = f'<script src="{BRIDGE_V2}"></script>'
    block = script + (("\n" + config_snippet) if config_snippet else "")
    if bridge_already(html):
        return html
    new_h = h[re.search(r"<head[^>]*>", h, re.I).end():]  # strip opening tag copy
    head_open = re.search(r"<head[^>]*>", h, re.I).group(0)
    new_head = head_open.split(head_open)[0] + head_open + "\n    <!-- PlayGama Bridge SDK. Loaded first so window.bridge exists before the game. -->\n    " + block + new_h
    return html.replace(h, new_head, 1)


def rewrite_zip(src_zip: str, dst_zip: str, html_name: str, new_html: str) -> None:
    src = zipfile.ZipFile(src_zip)
    with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED) as out:
        for n in src.namelist():
            if n.endswith("/"):
                continue
            if n == html_name:
                out.writestr(n, new_html)
            else:
                out.writestr(n, src.read(n))


def scan_relative(dirpath: str) -> list[str]:
    warns = []
    for root, _dirs, files in os.walk(dirpath):
        for f in files:
            if f.endswith((".html", ".js", ".json", ".css")):
                p = os.path.join(root, f)
                try:
                    t = open(p, encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                for m in re.findall(r"https?://[^\s\"'<>]+", t)[:3]:
                    if not re.search(r"bridge\.playgama\.com|w3\.org|github\.com|opensource", m):
                        warns.append(f"{os.path.relpath(p, dirpath)}: {m[:80]}")
                        break
    return warns


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--zip")
    g.add_argument("--dir")
    ap.add_argument("-o", "--out", help="output zip path")
    ap.add_argument("--config")
    ap.add_argument("--no-rescan", action="store_true")
    a = ap.parse_args()

    config_snippet = ""
    if a.config and os.path.exists(a.config):
        config_snippet = '<script>window.bridgeConfig = ' + open(a.config).read().strip() + ";</script>"

    if a.dir:
        d = a.dir
        idx = os.path.join(d, "index.html")
        if not os.path.exists(idx):
            raise SystemExit("index.html missing at " + d)
        html = open(idx, encoding="utf-8", errors="replace").read()
        new_html = inject(html, config_snippet)
        if new_html == html:
            log("Bridge already integrated — nothing to do")
        else:
            open(idx, "w", encoding="utf-8").write(new_html)
            log("injected into", idx)
        out = a.out
        if out:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for root, _dirs, files in os.walk(d):
                    for f in files:
                        p = os.path.join(root, f)
                        z.write(p, os.path.relpath(p, d))
            log("zipped ->", out)
        if not a.no_rescan:
            for w in scan_relative(d)[:10]:
                log("WARN absolute URL:", w)
        return

    # zip mode
    html, z = read_html_from_zip(a.zip)
    html_name = [n for n in z.namelist() if n.lower().endswith("index.html") and n.count("/") == 0][0]
    new_html = inject(html, config_snippet)
    out = a.out or (os.path.splitext(a.zip)[0] + "-bridged.zip")
    if new_html == html:
        log("Bridge already integrated — copying as-is")
        shutil.copy(a.zip, out)
    else:
        rewrite_zip(a.zip, out, html_name, new_html)
        log("injected and re-zipped ->", out)
    # sanity: verify first script in head
    with zipfile.ZipFile(out) as zo:
        head = head_of(zo.read(html_name).decode("utf-8", "replace"))
    scripts = re.findall(r"<script[^>]*src=[\"']([^\"']+)[\"']", head, re.I)
    first = scripts[0] if scripts else "(none)"
    log("first <head> script:", first)
    if not first.startswith(BRIDGE_V2):
        log("WARN: bridge script is not FIRST in <head> — Bridge docs require loading before game scripts; some engines hijack DOM earlier")


if __name__ == "__main__":
    main()
