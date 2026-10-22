#!/usr/bin/env python3
"""Read-only proof that the running gateway loaded the code now on disk.

Plugins log an exact content stamp when they register.  Older gateway processes
without stamps can still be proven stale when a runtime file is newer than the
last registration line; the reverse is only NOT_VERIFIABLE, never PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

OPENCLAW = Path(__file__).resolve().parents[2]  # repo root: skills/shared/<this file>
PLUGINS = OPENCLAW / "plugins"
LOG = Path.home() / "Library/Logs/openclaw/openclaw.log"
STAMP_LOCATION = Path(".lib/build-stamp-location.json")
STAMP_RE = re.compile(r"\[build-stamp\]\s+([\w-]+)\s+([a-f0-9]{64})\s+files=(.*)$")
REG_RE = re.compile(r"\[([\w-]+)\].*(?:handler registered|table loaded)")
RUNTIME_SUFFIXES = {".mjs", ".json"}
GENERATED_DATA_SUFFIX = ".generated.json"


def _when(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def runtime_files(directory):
    directory = Path(directory)
    return sorted(path for path in directory.iterdir() if path.is_file()
                  and path.suffix in RUNTIME_SUFFIXES
                  and not path.name.startswith("test-")
                  and not path.name.startswith("test.")
                  and not path.name.endswith(".test.mjs")
                  and not path.name.endswith(GENERATED_DATA_SUFFIX))


def stamp_sources(plugin_root):
    """Read the one helper-path declaration shared with the Node stamper."""
    plugin_root = Path(plugin_root)
    manifest = plugin_root / STAMP_LOCATION
    config = json.loads(manifest.read_text())
    helper_name = config.get("helper")
    hash_label = config.get("hash_label")
    manifest_label = config.get("manifest_label")
    if not all(isinstance(value, str) and value for value in
               (helper_name, hash_label, manifest_label)):
        raise ValueError("invalid build-stamp location manifest")
    helper = (plugin_root / helper_name).resolve()
    if plugin_root.resolve() not in helper.parents:
        raise ValueError("build-stamp helper must stay inside the plugin root")
    return {"helper": helper, "manifest": manifest, "hash_label": hash_label,
            "manifest_label": manifest_label}


def disk_stamp(directory):
    directory = Path(directory)
    sources = stamp_sources(directory.parent)
    digest = hashlib.sha256()
    digest.update(sources["hash_label"].encode() + b"\0")
    digest.update(sources["helper"].read_bytes())
    digest.update(b"\0" + sources["manifest_label"].encode() + b"\0")
    digest.update(sources["manifest"].read_bytes())
    files = runtime_files(directory)
    for path in files:
        digest.update(b"\0" + path.name.encode() + b"\0")
        digest.update(path.read_bytes())
    return {"hash": digest.hexdigest(), "files": [path.name for path in files]}


def _message(record):
    if isinstance(record.get("message"), str):
        return record["message"]
    values = [value for key, value in record.items() if key.isdigit() and isinstance(value, str)]
    return " ".join(values)


def parse_log(path=LOG):
    stamps, registrations, gateway_started = {}, {}, None
    target = Path(path)
    if not target.exists():
        return {"available": False, "stamps": {}, "registrations": {}, "gateway_started": None}
    for raw in target.read_text(errors="replace").splitlines():
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        when = _when((record.get("_meta") or {}).get("date") or record.get("time"))
        message = _message(record)
        if when and "http server listening" in message:
            gateway_started = when
        match = STAMP_RE.search(message)
        if match and when:
            stamps[match.group(1)] = {"hash": match.group(2), "files": match.group(3).split(","), "at": when}
        match = REG_RE.search(message)
        if match and when:
            registrations[match.group(1)] = when
    # Discard evidence from a prior process. Exact loaded stamps and fallback
    # registration clocks must belong to the current gateway start.
    if gateway_started:
        stamps = {key: value for key, value in stamps.items() if value["at"] >= gateway_started}
        registrations = {key: value for key, value in registrations.items() if value >= gateway_started}
    return {"available": True, "stamps": stamps, "registrations": registrations,
            "gateway_started": gateway_started}


def compare_plugins(plugin_root, *, stamps=None, registrations=None, gateway_started=None):
    plugin_root = Path(plugin_root); stamps = stamps or {}; registrations = registrations or {}
    rows = []
    for directory in sorted(path.parent for path in plugin_root.glob("*/openclaw.plugin.json")):
        plugin_id = directory.name
        try:
            sources = stamp_sources(plugin_root)
            disk = disk_stamp(directory)
            files = runtime_files(directory) + [sources["helper"], sources["manifest"]]
        except (OSError, ValueError) as exc:
            rows.append({"component": plugin_id, "status": "NOT_VERIFIABLE",
                         "reason": f"runtime source unavailable: {exc}"})
            continue
        loaded = stamps.get(plugin_id)
        if loaded:
            if loaded["hash"] == disk["hash"]:
                rows.append({"component": plugin_id, "status": "PASS", "hash": disk["hash"],
                             "reason": "loaded content stamp matches disk"})
            else:
                rows.append({"component": plugin_id, "status": "FAIL", "hash": disk["hash"],
                             "loaded_hash": loaded["hash"], "files": disk["files"],
                             "reason": "loaded content stamp differs from disk; restart the gateway"})
            continue
        loaded_at = registrations.get(plugin_id) or gateway_started
        if not loaded_at:
            rows.append({"component": plugin_id, "status": "NOT_VERIFIABLE",
                         "reason": "no loaded stamp, registration line, or gateway start is available"})
            continue
        newer = [str(path.relative_to(plugin_root.parent)) for path in files
                 if path.exists() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) > loaded_at]
        if newer:
            rows.append({"component": plugin_id, "status": "FAIL", "files": newer,
                         "reason": "runtime files changed after this gateway loaded; restart the gateway"})
        else:
            rows.append({"component": plugin_id, "status": "NOT_VERIFIABLE",
                         "reason": "gateway predates build-stamp logging; no newer file found, but equality is unproved"})
    return rows


def compare_gateway(bundle, gateway_started):
    if not bundle or not Path(bundle).exists() or not gateway_started:
        return {"component": "gateway bundle", "status": "NOT_VERIFIABLE",
                "reason": "gateway bundle path or current startup line is unavailable"}
    bundle = Path(bundle)
    files = [bundle] if bundle.is_file() else sorted(bundle.rglob("*.js"))
    newer = [str(path) for path in files
             if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) > gateway_started]
    if newer:
        return {"component": "gateway bundle", "status": "FAIL", "files": newer[:20],
                "reason": "installed gateway bundle changed after process start; restart the gateway"}
    return {"component": "gateway bundle", "status": "PASS",
            "reason": "installed gateway bundle has not changed since the logged process start"}


def gateway_status():
    try:
        run = subprocess.run(["openclaw", "gateway", "status", "--json"], capture_output=True,
                             text=True, timeout=15, check=False)
        return json.loads(run.stdout) if run.stdout.strip() else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def check(*, plugin_root=PLUGINS, log_path=LOG, status=None):
    evidence = parse_log(log_path)
    rows = compare_plugins(plugin_root, stamps=evidence["stamps"],
                           registrations=evidence["registrations"],
                           gateway_started=evidence["gateway_started"])
    status = gateway_status() if status is None else status
    args = (((status.get("service") or {}).get("command") or {}).get("programArguments") or [])
    bundle = next((value for value in args if str(value).endswith("/dist/index.js")), None)
    rows.append(compare_gateway(bundle, evidence["gateway_started"]))
    overall = "FAIL" if any(row["status"] == "FAIL" for row in rows) else (
        "NOT_VERIFIABLE" if any(row["status"] == "NOT_VERIFIABLE" for row in rows) else "PASS")
    return {"status": overall, "gateway_started": (evidence["gateway_started"].isoformat()
            if evidence["gateway_started"] else None), "components": rows,
            "remaining_step": "restart the gateway" if overall == "FAIL" else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-current", action="store_true",
                        help="Exit nonzero unless every component is proven current")
    args = parser.parse_args()
    result = check()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Gateway drift: {result['status']}")
        for row in result["components"]:
            print(f"{row['status']}: {row['component']} — {row['reason']}")
    if result["status"] == "FAIL" or (args.require_current and result["status"] != "PASS"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
