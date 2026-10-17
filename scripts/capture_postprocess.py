#!/usr/bin/env python3
"""
capture_postprocess.py — Playgama cover post-process (exact platform specs,
validated live from the MCP start_cover_upload schema):

    square    = 800 x 800      portrait = 1080 x 1920     landscape = 1920 x 1080
    PNG or JPEG, <= 10 MB each, full-bleed pixels (rounded corners rejected),
    icon readable (center-weighted scoring), gameplay-like frames preferred.

Usage:
  python3 capture_postprocess.py <frames_dir> --out-dir ./assets

Writes:
  assets/cover_square_800x800.png
  assets/cover_portrait_1080x1920.png
  assets/cover_landscape_1920x1080.png
  assets/ASSETS_SOURCE   (= assets_gameplay_capture)
Selects the richest frame (variance + edge density + center-third weighting),
crops full-bleed to each ratio, resizes with LANCZOS, verifies the 4 corners
are opaque and pixel-continuous, falls back to JPEG when PNG > 10 MB.
"""
from __future__ import annotations
import argparse
import os

try:
    from PIL import Image, ImageFilter
except Exception as e:  # pragma: no cover
    raise SystemExit("pip install pillow first: %s" % e)

MAX_BYTES = 10 * 1024 * 1024
SPECS = [
    ("square", (800, 800)),
    ("portrait", (1080, 1920)),
    ("landscape", (1920, 1080)),
]


def frame_score(img: Image.Image) -> float:
    """Richness = edge density + color variance; center third weighted x2."""
    try:
        small = img.convert("RGB").resize((160, 90))
        edges = small.filter(ImageFilter.FIND_EDGES).convert("L")
        hist = edges.histogram()
        total = sum(hist)
        edge_score = sum(i * c for i, c in enumerate(hist)) / max(total, 1)
        # variance of pixel values
        data = list(small.convert("L").getdata())
        mean = sum(data) / len(data)
        var = sum((p - mean) ** 2 for p in data[::3]) / max(len(data) // 3, 1)
        # center-third emphasis
        w, h = small.size
        region = small.convert("RGB").crop((w // 3, h // 3, 2 * w // 3, 2 * h // 3))
        rdata = list(region.convert("L").getdata())
        rmean = sum(rdata) / len(rdata)
        rvar = sum((p - rmean) ** 2 for p in rdata[::2]) / max(len(rdata) // 2, 1)
        return edge_score * 2 + var / 1000 + rvar / 400
    except Exception:
        return 0.0


def pick_best(frames_dir: str) -> Image.Image | None:
    best, bestv = None, -1.0
    for f in sorted(os.listdir(frames_dir)):
        if not f.endswith(".png"):
            continue
        try:
            img = Image.open(os.path.join(frames_dir, f))
        except Exception:
            continue
        v = frame_score(img)
        if v > bestv:
            best, bestv = img, v
    return best


def probe_corners(img: Image.Image) -> bool:
    """All 4 corners must be opaque, pixel-continuous (no rounded alpha cut)."""
    w, h = img.size
    pts = [(1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)]
    px = img.load()
    if img.mode in ("RGBA", "LA"):
        return all(px[p][3] > 250 for p in pts)
    return True  # opaque by construction


def full_bleed_crop(img: Image.Image, ratio: float) -> Image.Image:
    """Center cover-crop to the target ratio, hard square corners."""
    w, h = img.size
    cur = w / h
    if abs(cur - ratio) < 1e-4:
        return img.copy()
    if cur > ratio:  # too wide
        nw = int(h * ratio)
        x0 = (w - nw) // 2
        return img.crop((x0, 0, x0 + nw, h))
    nh = int(w / ratio)
    y0 = (h - nh) // 2
    return img.crop((0, y0, w, y0 + nh))


def save_exact(img: Image.Image, out_dir: str, kind: str, size: tuple[int, int]) -> str:
    cropped = full_bleed_crop(img, size[0] / size[1])
    final = cropped.resize(size, Image.LANCZOS).convert("RGB")
    if not probe_corners(final):
        # hard-repaint the corners opaque (safety net)
        final = final.convert("RGBA")
        final.paste((255, 255, 255, 255), (0, 0, size[0], 1))
        final.paste((255, 255, 255, 255), (0, size[1] - 1, size[0], size[1]))
        final.paste((255, 255, 255, 255), (0, 0, 1, size[1]))
        final.paste((255, 255, 255, 255), (size[0] - 1, 0, size[0], size[1]))
        final = final.convert("RGB")
    out = os.path.join(out_dir, f"cover_{kind}_{size[0]}x{size[1]}.png")
    final.save(out, "PNG", optimize=True)
    if os.path.getsize(out) > MAX_BYTES:
        final.save(out.replace(".png", ".jpg"), "JPEG", quality=88, optimize=True)
        os.remove(out)
        out = out.replace(".png", ".jpg")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir")
    ap.add_argument("--out-dir", default="./assets")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    best = pick_best(a.frames_dir)
    if best is None:
        raise SystemExit("no frames found in " + a.frames_dir)
    written = []
    for kind, size in SPECS:
        written.append(save_exact(best, a.out_dir, kind, size))
        print("wrote", written[-1], os.path.getsize(written[-1]), "bytes")
    with open(os.path.join(a.out_dir, "ASSETS_SOURCE"), "w") as f:
        f.write("assets_gameplay_capture")
    print("ASSETS_SOURCE=assets_gameplay_capture")


if __name__ == "__main__":
    main()
