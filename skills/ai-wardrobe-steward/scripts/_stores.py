#!/usr/bin/env python3
"""Store registry access shared by store_catalog.py and source_health.py.

config/stores.json is the user's list of stores: canonical slug, domain, how the catalog
is read, and every alias the scan has ever typed for the source.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "shared"))
sys.path.insert(0, str(ROOT / "scripts"))
from data_paths import path as data_path  # noqa: E402
from _store import load_json  # noqa: E402

STORES_PATH = data_path("stores.json")


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.strip().lower())).strip("-")


def load_stores() -> list:
    return load_json(STORES_PATH, {}).get("stores", [])


def alias_map(stores=None) -> dict:
    """lower-cased alias (and slug, and name) -> canonical slug."""
    out = {}
    for s in stores if stores is not None else load_stores():
        for a in [s["slug"], s.get("name", "")] + list(s.get("aliases", [])):
            if a:
                out[a.strip().lower()] = s["slug"]
                out[slugify(a)] = s["slug"]
    return out


def canonical(name: str, stores=None) -> str:
    """The one slug for a store however the scan named it; unknown names are slugified."""
    m = alias_map(stores)
    return m.get(name.strip().lower()) or m.get(slugify(name)) or slugify(name)


def by_slug(slug: str, stores=None) -> dict:
    for s in stores if stores is not None else load_stores():
        if s["slug"] == slug:
            return s
    raise KeyError(f"no store '{slug}' in {STORES_PATH}")
