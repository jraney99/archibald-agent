#!/usr/bin/env python3
"""Talk to the strips on the LAN instead of through Govee's cloud — and read back what they
are ACTUALLY showing.

    python3 govee_lan.py            # discover + print each strip's real state

WHY (2026-10-01): the cloud API lies in both directions. It reports "ok" for commands that
never reach the strip (the reason control.py applies everything twice), its /device/state
echoes the last command rather than the device, and that evening it called console_light
"Device is offline. Please check the Wi-Fi connection" while the strip was sitting happily on
the LAN answering every question we asked it. The user: "The API for Govee is awful."

Govee's LAN API (enabled per-device in the app → LAN Control, done 2026-10-01):
  discovery  multicast 239.255.255.250:4001  {"msg":{"cmd":"scan",...}}   → replies to :4002
  commands   udp <device ip>:4003            turn / brightness / colorwc  (no reply)
  state      udp <device ip>:4003            {"msg":{"cmd":"devStatus"}}  → reply to :4002

So a flat power/brightness/colour command can be SENT and then CONFIRMED against the device
itself, with a retry if it didn't take — which is what the blind double-apply was standing in
for. Scenes, gradient and DIY are cloud-only: the LAN API has no commands for them.

Falls back by raising LanUnavailable (no reply, or :4002 already held by another run) so the
caller can use the cloud. Discovered IPs are cached in state/govee_lan.json because DHCP
moves them; a cache miss or a silent device re-scans.
"""
import json
import socket
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "shared"))
from data_paths import path  # noqa: E402

CACHE = path("govee_lan.json")
MCAST_GROUP, SCAN_PORT, REPLY_PORT, CMD_PORT = "239.255.255.250", 4001, 4002, 4003
SCAN_SECONDS = 3.0
REPLY_TIMEOUT = 1.5
SETTLE_SECONDS = 0.6   # the strip needs a moment before devStatus reflects a command
CACHE_MAX_AGE = 86400  # re-scan at least daily, DHCP leases move


class LanUnavailable(Exception):
    """Local control didn't work for this device — the caller should use the cloud."""


def _reply_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    try:
        s.bind(("0.0.0.0", REPLY_PORT))
    except OSError as exc:  # another run holds it — don't fight over the port, use the cloud
        s.close()
        raise LanUnavailable(f"reply port {REPLY_PORT} unavailable ({exc})")
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 struct.pack("4sl", socket.inet_aton(MCAST_GROUP), socket.INADDR_ANY))
    return s


def _msg(cmd, data):
    return json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()


def scan(seconds=SCAN_SECONDS):
    """{device id: ip} for every strip answering local control."""
    found = {}
    rx = _reply_socket()
    try:
        rx.settimeout(0.5)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for _ in range(3):
            tx.sendto(_msg("scan", {"account_topic": "reserve"}), (MCAST_GROUP, SCAN_PORT))
            time.sleep(0.2)
        tx.close()
        end = time.time() + seconds
        while time.time() < end:
            try:
                raw, addr = rx.recvfrom(4096)
            except socket.timeout:
                continue
            data = _parse(raw)
            if data and data.get("device"):
                found[data["device"]] = data.get("ip") or addr[0]
    finally:
        rx.close()
    _save_cache(found)
    return found


def _parse(raw):
    try:
        return json.loads(raw).get("msg", {}).get("data") or {}
    except (ValueError, AttributeError):
        return None


def _load_cache():
    try:
        cached = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}
    if time.time() - cached.get("at", 0) > CACHE_MAX_AGE:
        return {}
    return cached.get("ips") or {}


def _save_cache(ips):
    if not ips:
        return
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"at": time.time(), "ips": {**_load_cache(), **ips}}, indent=1))


def ip_for(device, rescan=False):
    ips = {} if rescan else _load_cache()
    if device not in ips:
        ips = scan()
    if device not in ips:
        raise LanUnavailable(f"{device} did not answer local control (LAN Control off, or off the network)")
    return ips[device]


def status(device, ip=None):
    """What the strip itself says it is showing: {on, brightness, rgb, kelvin}."""
    ip = ip or ip_for(device)
    rx = _reply_socket()
    try:
        rx.settimeout(REPLY_TIMEOUT)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(_msg("devStatus", {}), (ip, CMD_PORT))
        tx.close()
        end = time.time() + REPLY_TIMEOUT
        while time.time() < end:
            try:
                raw, addr = rx.recvfrom(4096)
            except socket.timeout:
                break
            if addr[0] != ip:
                continue  # the other strip answering an overlapping run
            data = _parse(raw)
            if data is None or "onOff" not in data:
                continue
            colour = data.get("color") or {}
            return {"on": bool(data.get("onOff")), "brightness": data.get("brightness"),
                    "rgb": (colour.get("r"), colour.get("g"), colour.get("b")),
                    "kelvin": data.get("colorTemInKelvin")}
    finally:
        rx.close()
    raise LanUnavailable(f"{device} at {ip} didn't answer devStatus")


def send(device, ip=None, power=None, brightness=None, rgb=None, kelvin=None):
    """Fire the flat commands this device needs. No reply exists to wait for."""
    ip = ip or ip_for(device)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if power is not None:
            tx.sendto(_msg("turn", {"value": 1 if power else 0}), (ip, CMD_PORT))
            time.sleep(0.2)
        if rgb is not None:
            r, g, b = rgb
            tx.sendto(_msg("colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}), (ip, CMD_PORT))
            time.sleep(0.2)
        if kelvin:
            tx.sendto(_msg("colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin}), (ip, CMD_PORT))
            time.sleep(0.2)
        if brightness is not None:
            tx.sendto(_msg("brightness", {"value": int(brightness)}), (ip, CMD_PORT))
    finally:
        tx.close()
    return ip


def _matches(st, power, brightness, rgb, kelvin):
    if power is not None and st["on"] != bool(power):
        return False
    if brightness is not None and st["brightness"] != int(brightness):
        return False
    if rgb is not None and tuple(st["rgb"]) != tuple(rgb):
        return False
    if kelvin and st["kelvin"] != kelvin:
        return False
    return True


def apply(device, power=None, brightness=None, rgb=None, kelvin=None, tries=2):
    """Send, then confirm against the strip itself, retrying once. Raises LanUnavailable
    if local control isn't working — never if the strip simply disagrees, which returns the
    mismatch so the caller can say so out loud rather than reporting a false success."""
    ip = ip_for(device)
    last = None
    for attempt in range(tries):
        send(device, ip, power=power, brightness=brightness, rgb=rgb, kelvin=kelvin)
        time.sleep(SETTLE_SECONDS)
        last = status(device, ip)
        if _matches(last, power, brightness, rgb, kelvin):
            return {"transport": "lan", "ip": ip, "verified": True, "state": last, "attempts": attempt + 1}
    return {"transport": "lan", "ip": ip, "verified": False, "state": last, "attempts": tries}


def main():
    devices = json.loads((HERE.parent / "config" / "devices.json").read_text())["devices"]
    ips = scan()
    for nickname, dev in devices.items():
        try:
            st = status(dev["device"], ips.get(dev["device"]))
            print(f"{nickname:14} {ips.get(dev['device'], '?'):15} on={st['on']} brightness={st['brightness']} "
                  f"rgb={st['rgb']} kelvin={st['kelvin']}")
        except LanUnavailable as exc:
            print(f"{nickname:14} no local control: {exc}")


if __name__ == "__main__":
    main()
