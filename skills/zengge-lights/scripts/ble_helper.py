#!/usr/bin/env python3
"""The living-room light's Bluetooth helper: a launchd job that runs as Apple's signed Python
and does the radio work on behalf of control.py / status.py.

WHY (2026-10-12): macOS grants Bluetooth to the program at the TOP of a process tree. The user
granted Python, and that works when Python is launched on its own — but Archibald's scripts
run under the gateway, whose top process is Homebrew's node. macOS then judges node, which has
no grant and cannot be prompted, and CoreBluetooth sits in "unknown" forever. Granting node
would tie the permission to that exact build and silently die at the next upgrade. So the
radio lives here, in a job launchd starts directly as Python, and the scripts hand their
command over a local socket instead.

    ~/Library/LaunchAgents/ai.openclaw.zengge-ble-helper.plist  (source: ../launchd/)
    socket: state/zengge_helper.sock, mode 0600, one request per connection, one line of JSON
    each way:  {"op":"status"|"apply"|"ping", "name": <bluetooth name>, ...apply kwargs}
            -> {"ok":true,"result":...} | {"ok":false,"unavailable":true,"error":...}

It does nothing between requests (no polling, no scheduled work, no messages to the user). One
command at a time, so two callers can't both hold the light. If the helper is not running,
zengge_ble falls back to using the radio directly, which works from an interactive shell.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zengge_ble as zb  # noqa: E402

REQUEST_TIMEOUT = 10.0
_lock = asyncio.Lock()


async def serve_request(req: dict) -> dict:
    """One request -> one reply, as plain dicts (the socket layer is just JSON lines)."""
    try:
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "result": "pong"}
        name = req["name"]
        async with _lock:
            if op == "status":
                result = await zb._status(name)
            elif op == "apply":
                rgb = req.get("rgb")
                result = await zb._apply(name, power=req.get("power"), brightness=req.get("brightness"),
                                         rgb=tuple(rgb) if rgb else None, white=req.get("white"))
            else:
                return {"ok": False, "error": f"unknown op {op!r}"}
        return {"ok": True, "result": result}
    except zb.BleUnavailable as exc:
        return {"ok": False, "unavailable": True, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — the caller gets the reason, the helper stays up
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), REQUEST_TIMEOUT)
        reply = await serve_request(json.loads(line or b"{}"))
    except (asyncio.TimeoutError, ValueError) as exc:
        reply = {"ok": False, "error": f"bad request: {exc}"}
    try:
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
    finally:
        writer.close()


async def main() -> None:
    sock = zb.HELPER_SOCKET
    sock.parent.mkdir(parents=True, exist_ok=True)
    if sock.exists():
        sock.unlink()
    server = await asyncio.start_unix_server(_handle, path=str(sock))
    os.chmod(sock, 0o600)
    print(f"zengge ble helper listening on {sock} as {sys.executable}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
