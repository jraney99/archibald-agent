#!/usr/bin/env python3
"""Measure a garment's actual colour from its product photo.

  python3 color_from_image.py --url <image url> [--id <product id>] [--json]
  python3 color_from_image.py --file <path> [--json]

WHY (2026-10-13): the catalogs give colour NAMES, so matching "California Spruce" against
The user's Soft Summer palette meant guessing what the name means before `color_match.py` could
score it. The photo is right there, so the colour can be measured instead.

Method, and why it is shaped this way: product shots are a garment on a near-plain backdrop,
and the hard case is a CREAM garment on a WHITE background — dropping "near-white" would
delete the garment, and cream is the user's best colour. So the background is not assumed to be
white: it is read from the outer frame of the image, and pixels close to that colour are
dropped wherever they appear. What survives in the middle is the garment. `coverage` says
how much survived, and a low number means the reading should not be trusted.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json, save_json  # noqa: E402

CACHE = data_path("product_colors.json")
UA = "Mozilla/5.0 (Macintosh) ArchibaldWardrobe/1.0 (personal wardrobe; read-only)"
SIZE = 160           # enough detail for a colour, small enough to be instant
FRAME = 0.07         # the outer band read as background
CENTRE = 0.62        # the middle box the garment occupies
BG_DISTANCE = 42     # how close to the background a pixel must be to be dropped
BUCKET = 12          # rgb quantisation step


def fetch(url: str) -> bytes:
    curl = shutil.which("curl") or "/usr/bin/curl"
    r = subprocess.run([curl, "-sSL", "--max-time", "20", "-A", UA, url], capture_output=True)
    if r.returncode or not r.stdout:
        raise RuntimeError(f"could not fetch the image ({r.returncode})")
    return r.stdout


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def measure(data: bytes) -> dict:
    from PIL import Image
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as fh:
        fh.write(data); tmp = Path(fh.name)
    try:
        im = Image.open(tmp).convert("RGB")
        im.thumbnail((SIZE, SIZE))
        w, h = im.size
        px = im.load()
        fw, fh_ = max(1, int(w * FRAME)), max(1, int(h * FRAME))
        border = Counter()
        for x in range(w):
            for y in range(h):
                if x < fw or x >= w - fw or y < fh_ or y >= h - fh_:
                    border[tuple(v // BUCKET * BUCKET for v in px[x, y])] += 1
        background = border.most_common(1)[0][0] if border else (255, 255, 255)
        x0, x1 = int(w * (1 - CENTRE) / 2), int(w * (1 + CENTRE) / 2)
        y0, y1 = int(h * (1 - CENTRE) / 2), int(h * (1 + CENTRE) / 2)
        kept, total = Counter(), 0
        for x in range(x0, x1):
            for y in range(y0, y1):
                total += 1
                rgb = px[x, y]
                if _dist(rgb, background) < BG_DISTANCE:
                    continue
                kept[tuple(v // BUCKET * BUCKET for v in rgb)] += 1
        separated = bool(kept)
        if not kept:
            # The garment is the same colour as its backdrop, so nothing could be separated
            # from it. Read the middle of the frame anyway and report coverage 0, which is the
            # signal that this reading rests on an assumption rather than on a measurement.
            kept = Counter(tuple(v // BUCKET * BUCKET for v in px[x, y])
                           for x in range(x0, x1) for y in range(y0, y1))
        bucket, n = kept.most_common(1)[0]
        members = [c for c in kept.elements() if _dist(c, bucket) < BUCKET * 2]
        avg = tuple(round(sum(c[i] for c in members) / len(members)) for i in range(3))
        return {"hex": "#%02X%02X%02X" % avg, "rgb": list(avg),
                "coverage": round(sum(kept.values()) / total, 3) if (total and separated) else 0.0,
                "share": round(n / max(1, sum(kept.values())), 3),
                "background_hex": "#%02X%02X%02X" % background}
    finally:
        tmp.unlink(missing_ok=True)


def for_product(product_id, url: str, cache=None) -> dict:
    """Measured colour for a catalog product, fetched once and remembered."""
    store = cache if cache is not None else load_json(CACHE, {})
    key = str(product_id)
    if key in store:
        return store[key]
    out = measure(fetch(url))
    out["url"] = url
    store[key] = out
    if cache is None:
        save_json(CACHE, store)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url"); g.add_argument("--file")
    ap.add_argument("--id", help="catalog product id, to remember the reading")
    ap.add_argument("--match", action="store_true", help="also score it against his palette")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = for_product(a.id, a.url) if (a.id and a.url) else measure(fetch(a.url) if a.url else Path(a.file).read_bytes())
    if a.match:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "color_match.py"),
                            "--hex", res["hex"].lstrip("#")], capture_output=True, text=True)
        try:
            res["palette"] = json.loads(r.stdout)
        except Exception:  # noqa: BLE001
            res["palette_error"] = (r.stdout + r.stderr)[:120]
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        line = f"{res['hex']}  coverage {res['coverage']:.0%}  (backdrop {res['background_hex']})"
        v = (res.get("palette") or {}).get("verdict")
        print(line + (f"\n  palette: {v} — {(res.get('palette') or {}).get('reason', '')}" if v else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
