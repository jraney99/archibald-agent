#!/usr/bin/env python3
"""Match a candidate colour against the personal palette in
profile/color_analysis.yaml (DESIGN.md Module 11).

This is the deterministic grounding for two judgement calls elsewhere:
  - Shopping Advisor's colour-fit factor ("is this garment's colour actually
    one of mine?") — see SKILL.md.
  - The Outfit Engine's colour-harmony step (prefer priority families, keep
    combinations low-contrast for a Soft season).

The model still decides what colour a garment *is* (from a photo, a listing,
a description) and passes that in as a hex; this script only answers "how
close is that to the palette, and is it in or out." Distance is CIEDE2000
(perceptual), not raw RGB.

Usage:
  python3 color_match.py --hex "#4C98A2"
  python3 color_match.py --hex 4c98a2 --context leather
  python3 color_match.py --name "teal"            # fuzzy lookup by palette name
  python3 color_match.py --list-family blue-greens
  python3 color_match.py --families

Prints JSON. Exit 0 on a usable answer (including a "reject" verdict),
exit 1 only on bad input or a missing/empty color_analysis.yaml.
"""
import argparse
import difflib
import json
import math
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_PATH = ROOT / "profile" / "color_analysis.yaml"

# CIEDE2000 thresholds, tuned for hand-estimated hexes (the input is already
# approximate, so these are looser than a print-QA ΔE would be):
IN_PALETTE_MAX = 10.0      # <= this to the nearest palette swatch -> in-palette
BORDERLINE_MAX = 20.0      # <= this -> borderline; beyond -> reject (nothing close)
WARM_TIGHT_MATCH = 6.0     # a warm-undertone colour this close to a palette
                           # swatch is an exact palette brown (Mocha Brown etc.)
                           # and is kept; looser than that, the warm flag rejects.

# Warm-undertone gate in CIELCh: chroma above CHROMA_MIN and hue angle inside
# the yellow/gold/orange band. Catches golden / mustard / honey / camel /
# rust / terracotta / cognac without flagging cool reds (garnet, burgundy)
# or the teals.
WARM_CHROMA_MIN = 12.0
WARM_HUE_LOW = 40.0
WARM_HUE_HIGH = 100.0

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(s):
    m = HEX_RE.match(s.strip())
    if not m:
        sys.exit(f"'{s}' is not a 6-digit hex colour (e.g. #4C98A2)")
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb):
    r, g, b = (srgb_to_linear(v) for v in rgb)
    # linear sRGB -> XYZ (D65)
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    # normalise by D65 white
    x, y, z = x / 0.95047, y / 1.0, z / 1.08883

    def f(t):
        return t ** (1.0 / 3.0) if t > 216.0 / 24389.0 else (841.0 / 108.0) * t + 4.0 / 29.0

    fx, fy, fz = f(x), f(y), f(z)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def ciede2000(lab1, lab2):
    """Perceptual colour difference. Standard CIEDE2000 implementation."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    avg_Lp = (L1 + L2) / 2.0
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    avg_C = (C1 + C2) / 2.0
    G = 0.5 * (1 - math.sqrt((avg_C ** 7) / (avg_C ** 7 + 25.0 ** 7))) if avg_C > 0 else 0.0
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    avg_Cp = (C1p + C2p) / 2.0

    def hp(ap, bp):
        if ap == 0 and bp == 0:
            return 0.0
        ang = math.degrees(math.atan2(bp, ap))
        return ang + 360.0 if ang < 0 else ang

    h1p, h2p = hp(a1p, b1), hp(a2p, b2)

    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360.0
    else:
        dhp = h2p - h1p + 360.0

    dLp = L2 - L1
    dCp = C2p - C1p
    dHp = 2.0 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) / 2.0)

    if C1p * C2p == 0:
        avg_hp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        avg_hp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        avg_hp = (h1p + h2p + 360.0) / 2.0
    else:
        avg_hp = (h1p + h2p - 360.0) / 2.0

    T = (1
         - 0.17 * math.cos(math.radians(avg_hp - 30))
         + 0.24 * math.cos(math.radians(2 * avg_hp))
         + 0.32 * math.cos(math.radians(3 * avg_hp + 6))
         - 0.20 * math.cos(math.radians(4 * avg_hp - 63)))
    d_ro = 30.0 * math.exp(-(((avg_hp - 275.0) / 25.0) ** 2))
    Rc = 2.0 * math.sqrt((avg_Cp ** 7) / (avg_Cp ** 7 + 25.0 ** 7)) if avg_Cp > 0 else 0.0
    Sl = 1.0 + (0.015 * (avg_Lp - 50) ** 2) / math.sqrt(20 + (avg_Lp - 50) ** 2)
    Sc = 1.0 + 0.045 * avg_Cp
    Sh = 1.0 + 0.015 * avg_Cp * T
    Rt = -math.sin(math.radians(2 * d_ro)) * Rc

    return math.sqrt(
        (dLp / Sl) ** 2
        + (dCp / Sc) ** 2
        + (dHp / Sh) ** 2
        + Rt * (dCp / Sc) * (dHp / Sh)
    )


def lch_hue_chroma(lab):
    _, a, b = lab
    C = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a))
    return (h + 360.0 if h < 0 else h), C


def load_analysis():
    if not ANALYSIS_PATH.exists():
        sys.exit("No color_analysis.yaml on file — nothing to match against. "
                 "A colour analysis hasn't been captured for this profile yet.")
    with open(ANALYSIS_PATH) as f:
        data = yaml.safe_load(f) or {}
    if not data.get("palette"):
        sys.exit("color_analysis.yaml has no palette entries yet.")
    return data


def nearest(lab, swatches):
    """swatches: list of dicts with 'hex' (+ optional name/family/priority).
    Returns (entry, delta_e) for the closest, or (None, None) if empty."""
    best, best_d = None, None
    for s in swatches:
        d = ciede2000(lab, rgb_to_lab(parse_hex(s["hex"])))
        if best_d is None or d < best_d:
            best, best_d = s, d
    return best, best_d


def cmd_match(args):
    data = load_analysis()
    rgb = parse_hex(args.hex)
    lab = rgb_to_lab(rgb)
    hue, chroma = lch_hue_chroma(lab)
    input_hex = "#" + "".join(f"{v:02X}" for v in rgb)

    leather = args.context == "leather"
    pool = data.get("leather_palette", []) if leather else data["palette"]
    priority_families = set(data.get("priority_families", []))

    match, match_d = nearest(lab, pool)
    match_out = {
        "hex": match["hex"],
        "name": match.get("name"),
        "delta_e": round(match_d, 1),
    }
    if not leather:
        match_out["family"] = match.get("family")
        match_out["priority"] = match.get("family") in priority_families

    hard_reject = data.get("exclude", {}).get("hard_reject", [])
    rej, rej_d = nearest(lab, hard_reject) if hard_reject else (None, None)

    warm_flag = (not leather
                 and chroma >= WARM_CHROMA_MIN
                 and WARM_HUE_LOW <= hue <= WARM_HUE_HIGH
                 and match_d > WARM_TIGHT_MATCH)

    # verdict
    if rej is not None and rej_d < match_d and rej_d <= BORDERLINE_MAX:
        verdict = "reject"
        reason = (f"lands closer to the out-of-palette swatch {rej['name']} "
                  f"({rej['hex']}, ΔE {rej_d:.1f}) than to anything in the palette "
                  f"({match['name']}, ΔE {match_d:.1f})")
    elif warm_flag:
        verdict = "reject"
        reason = (f"reads warm/golden (hue {hue:.0f}°, chroma {chroma:.0f}) and isn't a "
                  f"tight palette match — the analysis rejects warm undertones even when "
                  f"the nearest swatch ({match['name']}, ΔE {match_d:.1f}) looks close")
    elif match_d <= IN_PALETTE_MAX:
        verdict = "in-palette"
        reason = f"matches {match['name']} ({match['hex']}) at ΔE {match_d:.1f}"
    elif match_d <= BORDERLINE_MAX:
        verdict = "borderline"
        reason = (f"nearest palette swatch is {match['name']} ({match['hex']}) at ΔE "
                  f"{match_d:.1f} — in the right area but not a clean match")
    else:
        verdict = "reject"
        reason = (f"nothing in the palette is close — nearest is {match['name']} "
                  f"({match['hex']}) at ΔE {match_d:.1f}")

    result = {
        "input": input_hex,
        "context": args.context,
        "verdict": verdict,
        "reason": reason,
        "nearest_palette": match_out,
        "warm_undertone_flag": warm_flag,
    }
    if rej is not None:
        result["nearest_excluded"] = {"hex": rej["hex"], "name": rej["name"], "delta_e": round(rej_d, 1)}
    if verdict in ("in-palette", "borderline") and not leather and match_out.get("priority"):
        result["note"] = f"{match_out['family']} is a priority family — rank this ahead of equal matches in other families."
    print(json.dumps(result, indent=2))


def cmd_name(args):
    data = load_analysis()
    entries = data["palette"] + [dict(e, family="leather") for e in data.get("leather_palette", [])]
    lower = args.name.lower()
    # substring matches first (the useful ones for "show me my teals"), then
    # fall back to fuzzy only if nothing contained the query
    hits = [e["name"] for e in entries if lower in e["name"].lower()]
    if not hits:
        hits = difflib.get_close_matches(args.name, [e["name"] for e in entries], n=8, cutoff=0.5)
    matched = [e for e in entries if e["name"] in hits]
    print(json.dumps({"query": args.name, "matches": matched}, indent=2))


def cmd_list_family(args):
    data = load_analysis()
    fam = args.list_family.lower()
    if fam == "leather":
        items = [dict(e, family="leather") for e in data.get("leather_palette", [])]
    else:
        items = [e for e in data["palette"] if e.get("family") == fam]
    if not items:
        sys.exit(f"No palette family '{args.list_family}'. "
                 f"Families: {sorted({e.get('family') for e in data['palette']})} (+ leather)")
    priority = fam in {f.lower() for f in data.get("priority_families", [])}
    print(json.dumps({"family": fam, "priority": priority, "colors": items}, indent=2))


def cmd_families(args):
    data = load_analysis()
    fams = sorted({e.get("family") for e in data["palette"]})
    print(json.dumps({
        "families": fams,
        "priority_families": data.get("priority_families", []),
        "has_leather_palette": bool(data.get("leather_palette")),
    }, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--hex", help="candidate colour as a 6-digit hex")
    g.add_argument("--name", help="fuzzy lookup of a palette colour by name")
    g.add_argument("--list-family", help="list every palette colour in a family (or 'leather')")
    g.add_argument("--families", action="store_true", help="list available palette families")
    p.add_argument("--context", choices=["clothing", "leather"], default="clothing",
                   help="'leather' checks against the leather goods list and skips the warm-undertone reject")
    args = p.parse_args()

    if args.hex:
        cmd_match(args)
    elif args.name:
        cmd_name(args)
    elif args.list_family:
        cmd_list_family(args)
    else:
        cmd_families(args)


if __name__ == "__main__":
    main()
