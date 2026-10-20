#!/usr/bin/env python3
"""The approved-course list: who may be booked, through which provider, with what defaults.

The list is config the user owns (config/approved_courses.json). A course that is not on it
does not exist to this skill - search_tee_times refuses it and propose_booking never
writes a pending file for it. A course that IS on it but sits on a provider without an
adapter is reported as exactly that, with its own booking link and phone number, so the
agent has a true thing to say instead of a guess.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from data_paths import path  # noqa: E402

BOOKABLE_PROVIDERS = ("golfnow",)


def load(config_path: Path = None) -> dict:
    cfg_path = config_path or path("approved_courses.json")
    return json.loads(Path(cfg_path).read_text())


def approved(cfg: dict) -> list:
    return [c for c in cfg.get("courses", []) if c.get("approved")]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).strip()


def find(cfg: dict, wanted: str):
    """The approved course the user means by `wanted` (id, name, short name or alias;
    case-insensitive; a unique substring also works). None if nothing matches, and a
    list if the name is ambiguous ('example creek' is an alias on West on purpose, so it
    resolves to the course he has actually played)."""
    w = _norm(wanted)
    if not w:
        return None
    courses = approved(cfg)
    for c in courses:
        names = [c.get("id", ""), c.get("name", ""), c.get("short", "")] + list(c.get("aliases", []))
        if w in {_norm(n) for n in names if n}:
            return c
    partial = [c for c in courses
               if any(w in _norm(n) for n in [c.get("name", ""), c.get("short", "")] + list(c.get("aliases", [])))]
    if len(partial) == 1:
        return partial[0]
    return partial or None


def bookable(course: dict) -> bool:
    return course.get("provider") in BOOKABLE_PROVIDERS and bool(course.get("facility_id"))


def not_bookable_reason(course: dict) -> str:
    """What to tell the user about an approved course this skill cannot book."""
    name = course.get("short") or course.get("name")
    bits = [f"{name} is on your approved list, but it is not sold through GolfNow, so I cannot book it yet."]
    if course.get("book_url"):
        bits.append(f"Book it here: {course['book_url']}")
    if course.get("phone"):
        bits.append(f"or call the shop at {course['phone']}.")
    return " ".join(bits)


def defaults(cfg: dict, course: dict) -> dict:
    """Skill defaults with any per-course override on top."""
    d = dict(cfg.get("defaults", {}))
    d.update(course.get("defaults", {}))
    return d
