#!/usr/bin/env python3
"""
File-locked, atomic read-modify-write for JSON state files.

Same locking pattern as nutrition/scripts/_state_lock.py: holds an
exclusive flock on a separate, never-replaced `.lock` file (not the data
file itself, since atomic writes swap the data file to a new inode) for
the entire read-modify-write block, then writes via temp file + os.replace.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def locked_state(path: Path, default: dict | list = None) -> Iterator:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)

    with open(lock_path, "r+") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            if path.exists():
                raw = path.read_text()
                try:
                    data = json.loads(raw) if raw.strip() else (default if default is not None else {})
                except json.JSONDecodeError as exc:
                    # Preserve the unreadable file for diagnosis. Replacing it
                    # with an empty default would erase alert/history state.
                    raise ValueError(f"invalid JSON state at {path}: {exc}") from exc
            else:
                data = default if default is not None else {}

            yield data

            tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
            try:
                with os.fdopen(tmp_fd, "w") as tmp_f:
                    json.dump(data, tmp_f, indent=2)
                os.replace(tmp_path, path)
            except Exception:
                os.unlink(tmp_path)
                raise
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
