#!/usr/bin/env python3
"""JSON load/save for the shopping path: atomic writes, loud failures, scripted repair.

Why this exists: on 2026-10-05 profile/source_health.json and on 2026-10-08
profile/product_history.json were each found as a valid document followed by the
tail of an older, longer version. Both are written by small scripts that used a
plain truncate-and-write while the watchlist scan ran several of them in parallel,
so one writer's truncate landed in the middle of another's write. mkstemp +
os.replace makes every write all-or-nothing, so that bug class cannot recur from
these scripts. A corrupt file still fails loudly with the repair spelled out rather
than surfacing as a bare traceback in a scan.

Use:  from _store import load_json, save_json
      rows = load_json(PATH, [])      # [] or {} says what shape is expected
      save_json(PATH, rows)
"""
import json
import os
import sys
import tempfile
from pathlib import Path


def load_json(path: Path, default):
    """Read `path`, or return `default` when it does not exist yet.

    Exits (never raises a bare traceback, never overwrites) when the file is not
    valid JSON or holds a different shape than `default`, naming the repair tool.
    """
    path = Path(path)
    if not path.exists():
        return default
    raw = path.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        repair = Path(__file__).resolve().parent / "repair_json.py"
        sys.exit(
            f"{path} is not valid JSON ({exc}).\n"
            f"Not touching it. Repair: python3 {repair} {path}\n"
            "(recovers the valid prefix, keeps the corrupt original under state/)."
        )
    if type(data) is not type(default):
        sys.exit(f"{path} holds {type(data).__name__}, expected {type(default).__name__}.")
    return data


def save_json(path: Path, data, **dump_kwargs) -> None:
    """Write `data` to `path` atomically: a reader sees the old file or the new one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_kwargs.setdefault("indent", 2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, **dump_kwargs)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def recover_prefix(raw: str):
    """The longest valid JSON document at the start of `raw`, plus the leftover text."""
    data, end = json.JSONDecoder().raw_decode(raw.lstrip())
    return data, raw.lstrip()[end:]
