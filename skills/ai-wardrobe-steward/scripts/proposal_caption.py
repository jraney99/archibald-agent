#!/usr/bin/env python3
"""The words and the 🚫 button on an outfit board. Pure: no network, no secrets.

Caption format and 🚫 labels approved by the user 2026-09-20 (Automation rollout gate).
"""
import re
from datetime import datetime


def event_name(title):
    return re.sub(r"\s*<>\s*the user\b", "", title or "").strip()


def when(start_iso):
    try:
        t = datetime.fromisoformat(start_iso)
    except (TypeError, ValueError):
        return start_iso or ""
    return f"{t.strftime('%a %b')} {t.day}, {t.strftime('%I:%M %p').lstrip('0')}"


def forecast_line(meta):
    f = (meta or {}).get("forecast")
    if not f:
        return "Forecast: not available"
    city = (f.get("place") or "").split(",")[0]
    return f"Forecast: {f['feels_like_f']}°F feels-like, wind {f['wind_mph']} mph, rain {f['rain_chance_pct']}%" + (f" ({city})" if city else "")


def piece_label(item):
    """Short human name for a closet piece: brand + colour + category."""
    if not item:
        return "?"
    colour = str(item.get("color") or "").split(",")[0].split(" / ")[0].strip()
    parts = [str(item.get("brand") or "").strip(), colour, str(item.get("category") or "").replace("_", " ")]
    label = " ".join(p for p in parts if p)
    if len(label) > 60:                      # cut on a word, never mid-word
        label = label[:60].rsplit(" ", 1)[0] + "…"
    return label or str(item.get("id"))


def status_lines(proposal):
    """What has happened to this board since it was sent — swaps, taps, the wear."""
    meta = proposal.get("meta") or {}
    lines = [f"Swapped: {s['old_label']} → {s['new_label']}" for s in meta.get("swaps", [])[-3:]]
    lines += [f"Noted: {n}" for n in meta.get("notes", [])[-3:]]
    status = proposal.get("status")
    if status == "worn":
        lines.append("✅ Worn, logged.")
    elif status in ("skip", "worn-different"):
        lines.append("👕 Wore something else.")
    return lines


CATEGORY_NOUN = {"specialty_footwear": "golf shoes", "athletic_shoes": "trainers", "dress_shoes": "dress shoes",
                 "t-shirt": "tee", "suit": "suit"}
BRAND_SHORT = {"Holderness & Bourne": "H&B", "State & Liberty": "S&L", "Polo Ralph Lauren": "Ralph Lauren"}
SLOT_ORDER = {"shirt": 0, "t-shirt": 0, "polo": 0, "suit": 0, "sweater": 1, "vest": 2, "jacket": 2, "coat": 2,
              "blazer": 2, "pants": 3, "jeans": 3, "shorts": 3, "activewear": 3, "sneakers": 4, "boots": 4,
              "loafers": 4, "dress_shoes": 4, "athletic_shoes": 4, "specialty_footwear": 4, "belt": 5}


def _short_colour(value):
    text = str(value or "").split(",")[0].split(" / ")[0].split(" with ")[0].strip()
    words = [w for w in text.split() if w.lower() not in ("heather", "light", "dark", "washed", "faded")] or text.split()
    return " ".join(words[:2])


def piece_line(item):
    """'H&B navy belt' — brand, colour, what it is. The board is photos only, so this names them."""
    if not item:
        return "?"
    brand = str(item.get("brand") or "").split("(")[0].strip()
    brand = BRAND_SHORT.get(brand, brand)
    noun = CATEGORY_NOUN.get(item.get("category"), str(item.get("category") or "").replace("_", " "))
    return " ".join(x for x in (brand, _short_colour(item.get("color")), noun) if x)[:46]


def short_when(start_iso):
    try:
        t = datetime.fromisoformat(start_iso)
    except (TypeError, ValueError):
        return start_iso or ""
    return f"{t.strftime('%a')} {t.strftime('%I:%M%p').lstrip('0').lower().replace(':00', '')}"


def caption(proposal, closet=None):
    """The words under an outfit board.

    Rebuilt 2026-10-13 after the user: "I dont really like the way it has looked." The old
    caption was an event line, a leaked triage reason ("Why: a real round of golf"), a
    50-word stylist paragraph, and a forecast line repeating a temperature the paragraph
    had already given — while never naming a single piece. He chose option B: the board
    is photos only and these words name them, once.
    """
    meta = proposal.get("meta") or {}
    if not meta.get("why"):          # proposals created before 2026-09-20
        assumption = f"\nAssumption: {proposal['assumption']}" if proposal.get("assumption") else ""
        weather = f"\nWeather: {proposal['weather']}" if proposal.get("weather") else ""
        return f"{proposal['event_title']} — {proposal['event_start']}\n{proposal['reason']}{assumption}{weather}"[:1000]
    head = f"👔 {event_name(proposal['event_title']).split(' @ ')[0].strip()} · {short_when(proposal['event_start'])}"
    feels = ((meta.get("forecast") or {}).get("feels_like_f"))
    if feels is not None:
        head += f" · {round(feels)}°F"
    lines = [head]
    if closet is None:
        closet = _load_closet()
    by_id = {i.get("id"): i for i in closet}
    ordered = sorted(proposal.get("items", []), key=lambda i: SLOT_ORDER.get((by_id.get(i) or {}).get("category"), 9))
    lines += [f"• {piece_line(by_id.get(i)) if by_id.get(i) else i}" for i in ordered]
    why = " ".join(str(proposal.get("reason") or "").split())
    if why:
        lines.append(why if len(why) <= 120 else why[:117].rsplit(" ", 1)[0] + "…")
    tail = status_lines(proposal)
    return "\n".join(lines + tail)[:1000]


def _load_closet():
    import json
    from pathlib import Path as _P
    p = _P(__file__).resolve().parent.parent / "profile" / "closet.json"
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001 - a caption must never fail over the closet file
        return []


def first_name(person):
    name = (person or {}).get("name") or ((person or {}).get("email") or "").split("@")[0]
    return name.split()[0] if name else "them"


def never_label(meta):
    """What ONE tap on the 🚫 button switches off, said plainly on the button itself."""
    kind = (meta or {}).get("kind") or ""
    if kind.startswith("meeting-"):
        return f"🚫 No outfits for {first_name(meta.get('person'))}"
    return {"dining": f"🚫 Not for {(meta.get('venue_name') or 'this place')[:24]}", "flight": "🚫 No flight-day outfits",
            "hosted-event": "🚫 Not for events like this", "golf-round": "🚫 No golf-round outfits"}.get(kind)


def never_ack(meta):
    kind = (meta or {}).get("kind") or ""
    if kind.startswith("meeting-"):
        return f"Understood — no more outfits for meetings with {first_name(meta.get('person'))}. 🎩"
    return {"dining": f"Understood — no more outfits for {(meta.get('venue_name') or 'that place')[:40]}. 🎩",
            "flight": "Understood — no more flight-day outfits. 🎩", "hosted-event": "Understood — no more outfits for events like this. 🎩",
            "golf-round": "Understood — no more golf-round outfits. 🎩"}.get(kind, "Understood. 🎩")
