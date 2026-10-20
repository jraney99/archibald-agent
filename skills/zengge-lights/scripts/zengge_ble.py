#!/usr/bin/env python3
"""Talk to a Zengge (ZENGGE app / "LEDnetWF") Bluetooth light from this Mac, and read back
what it is ACTUALLY showing.

    python3 zengge_ble.py            # scan + print each configured light's real state

The user's living-room light (added 2026-10-12) pairs with the ZENGGE app, which means
Bluetooth only: no Wi-Fi, no cloud, no LAN API. The Mac mini's own radio talks to it, so the
light has to be within Bluetooth range of the Mac, and the phone app must not be holding the
connection at the same time (a BLE device takes one central at a time).

Protocol: reverse-engineered by the lednetwf_ble Home Assistant project
(github.com/8none1/lednetwf_ble, protocol_docs/ + ai_instructions/DISCOVERIES.md). This light
is product 0x27 ("Ctrl_Mini_RGBW") on BLE protocol v5, firmware 20 — the exact unit that
project documented on 2026-10-01, with these quirks that matter here:
  - the classic 0x31 colour command is accepted and IGNORED; only the unified 0x3B family
    works (power 0x23/0x24, colour 0xA1, white 0xB1, brightness 0x01);
  - in the 0xA1 colour command bytes 7-9 are a DELAY in ms, so they must be zero — with RGB
    there, red colours arrive hours later and look "ignored";
  - brightness rides inside the colour command; the light stores the colour pre-scaled
    ((255,43,0) at 30% reads back as (77,13,0)), so the read-back is compared against the
    scaled colour. Never send a separate 0x3B 0x01 brightness right after a colour: it
    rescales the colour the light still holds and cancels the change;
  - it has ONE white channel, so there is no colour temperature — "white" is white at a
    brightness, the 0xB1 command at 0% temperature.

Every write is wrapped in an 8-byte transport header with a sequence number (a repeated
sequence can be dropped). The state query 0x81 is answered on the notify characteristic,
JSON-wrapped on this firmware: {"code":0,"payload":"<hex>"}.

Confirmation, not hope: apply() sends, waits, asks the light what it shows, and retries once
if it disagrees. A light that still disagrees is reported as unverified, never as success.
BleUnavailable means the radio/light could not be reached at all (Bluetooth off, out of
range, the app holding it, or a sandboxed process without Bluetooth access).

Who holds the radio (2026-10-12): macOS grants Bluetooth to the TOP process of a tree, and under
the gateway that is Homebrew's node, which has no grant. So status()/apply() first hand the
request to ble_helper.py — a launchd job running as Apple's signed Python, the identity the user
granted — over state/zengge_helper.sock, and only use the radio directly when no helper is
listening (an interactive shell). ZENGGE_NO_HELPER=1 forces the direct path.
"""
from __future__ import annotations

import asyncio
import colorsys
import json
import os
import socket
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "shared"))
from data_paths import path  # noqa: E402

DEVICES = path("zengge_devices.json")
HELPER_SOCKET = path("zengge_helper.sock")
HELPER_TIMEOUT = 60.0   # a command is ~3-5 s; a retry and a slow scan still fit

SERVICE_UUID = "0000ffff-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
ZENGGE_COMPANY_IDS = range(0x5A00, 0x5B00)

FIND_TIMEOUT = 10.0     # seconds to wait for the light's advertisement
CONNECT_TIMEOUT = 20.0
REPLY_TIMEOUT = 3.0     # the 0x81 answer normally lands within ~0.2 s
SETTLE_SECONDS = 0.6    # the light needs a moment before devStatus reflects a command
RGB_TOLERANCE = 6       # hue/sat go over as integers, so a channel can drift a few units
PCT_TOLERANCE = 2


class BleUnavailable(Exception):
    """The light could not be reached over Bluetooth — not a disagreement about state."""


# ---------------------------------------------------------------------------------------
# Wire format (pure functions, covered by test_zengge_ble.py)
# ---------------------------------------------------------------------------------------

def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def wrap(cmd: bytes, seq: int, expect_reply: bool) -> bytes:
    """The 8-byte transport header every command needs; raw command bytes are ignored."""
    n = len(cmd)
    return bytes([0x00, seq & 0xFF, 0x80, 0x00, (n >> 8) & 0xFF, n & 0xFF, (n + 1) & 0xFF,
                  0x0A if expect_reply else 0x0B]) + cmd


def unwrap(data: bytes) -> bytes | None:
    """The command payload inside a notification, JSON-wrapped or not."""
    if len(data) < 9:
        return None
    payload = bytes(data[8:])
    if payload[0] in (0x7B, 0x5B):  # '{' or '['
        try:
            obj = json.loads(payload.decode("utf-8", errors="ignore"))
            return bytes.fromhex(obj["payload"]) if isinstance(obj, dict) and "payload" in obj else None
        except (ValueError, KeyError):
            return None
    return payload


def _with_checksum(body: list[int]) -> bytes:
    return bytes(body + [checksum(bytes(body))])


def cmd_power(on: bool) -> bytes:
    return _with_checksum([0x3B, 0x23 if on else 0x24, 0, 0, 0, 0, 0, 0, 0, 0x32, 0, 0])


def cmd_color(r: int, g: int, b: int, brightness: int) -> bytes:
    """Unified colour: packed hue+sat, brightness 0-100, bytes 7-9 ZERO (they are a delay)."""
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    packed = (int(h * 360) << 7) | int(s * 100)
    return _with_checksum([0x3B, 0xA1, (packed >> 8) & 0xFF, packed & 0xFF,
                           max(1, min(100, brightness)), 0, 0, 0, 0, 0, 0, 0])


def cmd_white(brightness: int) -> bytes:
    """The single white channel at a brightness (0xB1 at 0% temperature)."""
    return _with_checksum([0x3B, 0xB1, 0, 0, 0, 0, max(1, min(100, brightness)), 0, 0, 0, 0, 0])


def cmd_brightness(pct: int) -> bytes:
    """Brightness alone, keeping the current colour/white. Never pair it with cmd_color."""
    pct = max(1, min(100, pct))
    return _with_checksum([0x3B, 0x01, 0, 0, pct, 0, pct, 0, 0, 0, 0, 0])


def cmd_query() -> bytes:
    return bytes([0x81, 0x8A, 0x8B, 0x40])


def parse_state(payload: bytes) -> dict | None:
    """The 14-byte 0x81 answer -> {on, mode, rgb, white, brightness}. None if it is not one."""
    if len(payload) < 14 or payload[0] != 0x81 or checksum(payload[:13]) != payload[13]:
        return None
    on = payload[2] == 0x23
    mode_type, flag = payload[3], payload[12]
    r, g, b, white = payload[6], payload[7], payload[8], payload[9]
    if mode_type == 0x61 and flag == 0xF0:
        mode, brightness = "rgb", round(max(r, g, b) * 100 / 255)
    elif mode_type == 0x61 and flag == 0x0F:
        mode, brightness = "white", round(white * 100 / 255)
    elif mode_type == 0x61 and flag == 0x5A:
        mode, brightness = "fading", None
    elif mode_type == 0x25 or 37 <= mode_type <= 56:
        mode, brightness = "effect", None
    else:
        mode, brightness = "unknown", None
    return {"on": on, "mode": mode, "rgb": (r, g, b), "white": white, "brightness": brightness,
            "raw": payload.hex()}


def parse_advertisement(name: str, manufacturer_data: dict) -> dict | None:
    """What a Zengge light says about itself in its advertisement, or None for anything else."""
    for cid, md in manufacturer_data.items():
        if cid in ZENGGE_COMPANY_IDS and len(md) >= 14:
            ble_version = md[1]
            out = {"name": name, "ble_version": ble_version,
                   "mac": ":".join(f"{x:02X}" for x in md[2:8]),
                   "product_id": (md[8] << 8) | md[9], "firmware": md[10]}
            if ble_version >= 5 and len(md) > 14:
                out["on"] = md[14] == 0x23
            return out
    return None


def scaled(rgb, brightness: int):
    """The colour the light stores for an rgb at a brightness."""
    return tuple(round(c * brightness / 100) for c in rgb)


def _close(a, b, tol) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _matches(st: dict, power=None, brightness=None, rgb=None, white=None) -> bool:
    """Does the light's own report agree with what we asked for? Fields not asked about are
    not judged."""
    if st is None:
        return False
    if power is not None and st["on"] != bool(power):
        return False
    if power is False:
        return True  # an off light reports whatever it last showed; nothing else to judge
    if rgb is not None:
        want = scaled(rgb, brightness if brightness is not None else 100)
        if st["mode"] != "rgb" or any(not _close(a, b, RGB_TOLERANCE) for a, b in zip(st["rgb"], want)):
            return False
    if white is not None:
        if st["mode"] != "white" or not _close(st["brightness"], brightness, PCT_TOLERANCE):
            return False
    if brightness is not None and rgb is None and white is None \
            and not _close(st["brightness"], brightness, PCT_TOLERANCE):
        return False
    return True


# ---------------------------------------------------------------------------------------
# Radio
# ---------------------------------------------------------------------------------------

class Session:
    """One Bluetooth connection to one light. Use as `async with`."""

    def __init__(self, name: str):
        self.name = name
        self.address = None
        self._client = None
        self._seq = 0
        self._replies: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self):
        try:
            from bleak import BleakClient, BleakScanner
            from bleak.exc import BleakError
        except ImportError as exc:
            raise BleUnavailable(f"bleak is not installed for {sys.executable}: {exc}")
        try:
            device = await BleakScanner.find_device_by_name(self.name, timeout=FIND_TIMEOUT)
        except BleakError as exc:
            raise BleUnavailable(f"Bluetooth is not usable from this process ({exc})")
        if device is None:
            raise BleUnavailable(f"{self.name} is not advertising (off at the switch, out of range of "
                                 f"the Mac, or the ZENGGE app is holding the connection)")
        self.address = device.address
        client = BleakClient(device, timeout=CONNECT_TIMEOUT)
        try:
            await client.connect()
            await client.start_notify(NOTIFY_UUID, self._on_notify)
        except (BleakError, asyncio.TimeoutError, OSError) as exc:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            raise BleUnavailable(f"could not connect to {self.name} at {self.address}: {exc}")
        self._client = client
        return self

    async def __aexit__(self, *exc):
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001 — we are leaving anyway
                pass

    def _on_notify(self, _handle, data):
        self._replies.put_nowait(bytes(data))

    async def send(self, cmd: bytes, expect_reply: bool = False) -> None:
        self._seq = (self._seq + 1) & 0xFF
        await self._client.write_gatt_char(WRITE_UUID, wrap(cmd, self._seq, expect_reply), response=False)

    async def query(self) -> dict:
        while not self._replies.empty():
            self._replies.get_nowait()
        await self.send(cmd_query(), expect_reply=True)
        end = time.monotonic() + REPLY_TIMEOUT
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise BleUnavailable(f"{self.name} did not answer the state query")
            try:
                raw = await asyncio.wait_for(self._replies.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
            st = parse_state(unwrap(raw) or b"")
            if st is not None:
                return st


def _session(name: str):
    """Factory so tests can stand in a fake light."""
    return Session(name)


async def _status(name: str) -> dict:
    async with _session(name) as s:
        st = await s.query()
        return {**st, "address": s.address}


async def _apply(name: str, power=None, brightness=None, rgb=None, white=None, tries=2) -> dict:
    async with _session(name) as s:
        current = await s.query()
        level = brightness
        if level is None and (rgb is not None or white is not None):
            # keep the light's current level when only the colour changes; a colour command
            # always carries a brightness, so there is no "unchanged" value to send
            level = current["brightness"] or 100
        last = current
        for attempt in range(tries):
            if power is True:
                await s.send(cmd_power(True))
                await asyncio.sleep(0.3)
            if rgb is not None:
                await s.send(cmd_color(*rgb, level))
            elif white is not None:
                await s.send(cmd_white(level))
            elif brightness is not None:
                await s.send(cmd_brightness(brightness))
            if power is False:
                await s.send(cmd_power(False))
            await asyncio.sleep(SETTLE_SECONDS)
            last = await s.query()
            if _matches(last, power=power, brightness=level if (rgb is not None or white is not None) else brightness,
                        rgb=rgb, white=white):
                return {"transport": "ble", "address": s.address, "verified": True, "state": last,
                        "attempts": attempt + 1}
        return {"transport": "ble", "address": s.address, "verified": False, "state": last, "attempts": tries}


def _via_helper(req: dict):
    """The helper's answer, or None when no helper is listening (then use the radio here)."""
    if os.environ.get("ZENGGE_NO_HELPER"):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(HELPER_TIMEOUT)
    try:
        s.connect(str(HELPER_SOCKET))
    except OSError:  # no socket file, nobody listening, or a stale socket
        s.close()
        return None
    try:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        raise BleUnavailable("the Bluetooth helper did not answer in time")
    finally:
        s.close()
    try:
        reply = json.loads(buf or b"{}")
    except ValueError:
        raise BleUnavailable("the Bluetooth helper sent an unreadable reply")
    if not reply.get("ok"):
        raise BleUnavailable(reply.get("error") or "the Bluetooth helper failed")
    result = reply.get("result")
    if isinstance(result, dict):  # JSON turned the rgb tuples into lists
        inner = result.get("state") if isinstance(result.get("state"), dict) else None
        for st in (result, inner):
            if st and isinstance(st.get("rgb"), list):
                st["rgb"] = tuple(st["rgb"])
    return result


def status(name: str) -> dict:
    """What the light itself says it is showing."""
    via = _via_helper({"op": "status", "name": name})
    return via if via is not None else asyncio.run(_status(name))


def apply(name: str, power=None, brightness=None, rgb=None, white=None, tries=2) -> dict:
    """Send, then confirm against the light itself, retrying once. Raises BleUnavailable if the
    light can't be reached — never if it simply disagrees, which comes back as verified=False
    so the caller can say so out loud rather than report a false success."""
    via = _via_helper({"op": "apply", "name": name, "power": power, "brightness": brightness,
                       "rgb": list(rgb) if rgb else None, "white": white})
    if via is not None:
        return via
    return asyncio.run(_apply(name, power=power, brightness=brightness, rgb=rgb, white=white, tries=tries))


def load_devices() -> dict:
    return json.loads(DEVICES.read_text())["devices"]


def render(rgb, brightness):
    """How these lamps should show a colour the Govee strips show (config `renditions`): the
    tuned colour for an exact match, with the brightness scaled; anything else unchanged."""
    try:
        table = json.loads(DEVICES.read_text()).get("renditions") or {}
    except (OSError, ValueError):
        table = {}
    key = ",".join(str(int(c)) for c in rgb) if rgb is not None else None
    hit = table.get(key)
    if not hit:
        return rgb, brightness
    out_rgb = tuple(int(c) for c in hit["color"].split(","))
    scale = float(hit.get("brightness_scale", 1.0))
    out_b = None if brightness is None else max(1, min(100, round(brightness * scale)))
    return out_rgb, out_b


async def _scan(seconds: float = 8.0) -> list[dict]:
    from bleak import BleakScanner
    seen = {}

    def cb(device, adv):
        parsed = parse_advertisement(adv.local_name or device.name or "", adv.manufacturer_data)
        if parsed:
            seen[device.address] = {**parsed, "address": device.address, "rssi": adv.rssi}

    scanner = BleakScanner(cb)
    await scanner.start()
    await asyncio.sleep(seconds)
    await scanner.stop()
    return sorted(seen.values(), key=lambda d: -d["rssi"])


def scan(seconds: float = 8.0) -> list[dict]:
    """Every Zengge light advertising near the Mac, strongest signal first."""
    return asyncio.run(_scan(seconds))


def main() -> None:
    devices = load_devices()
    for nickname, dev in devices.items():
        try:
            st = status(dev["name"])
            print(f"{nickname:14} {st['address']}  on={st['on']} mode={st['mode']} "
                  f"brightness={st['brightness']} rgb={st['rgb']} white={st['white']}")
        except BleUnavailable as exc:
            print(f"{nickname:14} unreachable: {exc}")


if __name__ == "__main__":
    main()
