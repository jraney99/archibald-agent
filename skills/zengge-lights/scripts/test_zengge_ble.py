#!/usr/bin/env python3
"""The Zengge Bluetooth light: wire format, read-back parsing, and confirm-or-say-so.

No radio is touched: the bytes are checked against the frames captured from the user's own
living-room light on 2026-10-12 and the lednetwf_ble protocol notes, and apply() is run
against a fake light.

    python3 test_zengge_ble.py
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# dir=/tmp: a unix socket path must stay under 104 chars, and $TMPDIR is long on macOS
os.environ.setdefault("OPENCLAW_HOME", tempfile.mkdtemp(prefix="zengge-test-", dir="/tmp"))
os.environ.pop("ZENGGE_NO_HELPER", None)
_SCRIPTS = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


zb = _load("zengge_ble", _SCRIPTS / "zengge_ble.py")
helper = _load("ble_helper", _SCRIPTS / "ble_helper.py")
fade = _load("evening_fade", _SCRIPTS / "evening_fade.py")

# The living-room light's own answer to the state query, 2026-10-12 (JSON-wrapped on this
# firmware), and its advertisement from the same minute (address bytes replaced with a placeholder).
NOTIFY_RAW = bytes.fromhex("041280000033340a" + "7b22636f6465223a302c227061796c6f6164223a22"
                           "38313237323336313136304639354646303030303031303046304436227d")
STATE_FRAME = bytes.fromhex("8127236116 0F95FF00 00 01 00 F0 D6".replace(" ", ""))
ADVERT = {0x5A00: bytes.fromhex("5505aabbccddeeff0027140101012361160f95ff00000100f00000")}


class Wire(unittest.TestCase):
    def test_transport_header_matches_the_documented_example(self):
        # protocol_docs/04: state query, seq 1, expects a reply
        self.assertEqual(zb.wrap(zb.cmd_query(), 1, True).hex(), "000180000004050a818a8b40")

    def test_every_command_ends_in_its_checksum(self):
        for cmd in (zb.cmd_power(True), zb.cmd_power(False), zb.cmd_color(255, 170, 90, 30),
                    zb.cmd_white(60), zb.cmd_brightness(40)):
            self.assertEqual(cmd[-1], sum(cmd[:-1]) & 0xFF)
            self.assertEqual(len(cmd), 13)

    def test_power_uses_the_unified_0x3b_form_this_firmware_honours(self):
        self.assertEqual(zb.cmd_power(True)[:2], bytes([0x3B, 0x23]))
        self.assertEqual(zb.cmd_power(False)[:2], bytes([0x3B, 0x24]))

    def test_colour_carries_brightness_and_keeps_the_delay_bytes_zero(self):
        cmd = zb.cmd_color(255, 43, 0, 30)
        self.assertEqual(cmd[:2], bytes([0x3B, 0xA1]))
        self.assertEqual(cmd[4], 30)
        # bytes 7-9 are a delay in ms on product 0x27: red there = hours of "ignored"
        self.assertEqual(cmd[7:12], bytes(5))
        packed = (cmd[2] << 8) | cmd[3]
        self.assertEqual(packed >> 7, 10)       # hue of (255,43,0) is 10°
        self.assertEqual(packed & 0x7F, 100)    # fully saturated

    def test_white_is_the_single_channel_at_zero_temperature(self):
        cmd = zb.cmd_white(60)
        self.assertEqual(cmd[:2], bytes([0x3B, 0xB1]))
        self.assertEqual((cmd[5], cmd[6]), (0, 60))

    def test_brightness_is_clamped_to_1_100(self):
        self.assertEqual(zb.cmd_brightness(0)[4], 1)
        self.assertEqual(zb.cmd_brightness(250)[4], 100)
        self.assertEqual(zb.cmd_color(0, 0, 255, 0)[4], 1)


class ReadBack(unittest.TestCase):
    def test_the_lights_json_wrapped_reply_unwraps_to_the_state_frame(self):
        self.assertEqual(zb.unwrap(NOTIFY_RAW), STATE_FRAME)

    def test_a_bare_binary_reply_is_returned_as_is(self):
        self.assertEqual(zb.unwrap(bytes(8) + STATE_FRAME), STATE_FRAME)
        self.assertIsNone(zb.unwrap(b"short"))

    def test_the_captured_frame_parses_to_what_the_light_was_showing(self):
        st = zb.parse_state(STATE_FRAME)
        self.assertEqual((st["on"], st["mode"], st["rgb"], st["white"], st["brightness"]),
                         (True, "rgb", (0x95, 0xFF, 0x00), 0, 100))

    def test_white_mode_reads_its_level_from_the_white_channel(self):
        frame = bytearray(STATE_FRAME)
        frame[6:9] = b"\x00\x00\x00"
        frame[9] = 128
        frame[12] = 0x0F
        frame[13] = zb.checksum(bytes(frame[:13]))
        st = zb.parse_state(bytes(frame))
        self.assertEqual((st["mode"], st["brightness"]), ("white", 50))

    def test_a_corrupt_or_foreign_frame_is_not_a_state(self):
        bad = bytearray(STATE_FRAME)
        bad[13] ^= 1
        self.assertIsNone(zb.parse_state(bytes(bad)))
        self.assertIsNone(zb.parse_state(b"\x82" + STATE_FRAME[1:]))
        self.assertIsNone(zb.parse_state(STATE_FRAME[:10]))

    def test_the_advertisement_identifies_the_living_room_light(self):
        adv = zb.parse_advertisement("LEDnetWF020027DDEEFF", ADVERT)
        self.assertEqual(adv["product_id"], 0x27)
        self.assertEqual(adv["ble_version"], 5)
        self.assertEqual(adv["mac"], "AA:BB:CC:DD:EE:FF")
        self.assertTrue(adv["on"])
        self.assertIsNone(zb.parse_advertisement("Govee_H61E6_0001", {0x8803: bytes.fromhex("ec00010101")}))


class Matching(unittest.TestCase):
    def state(self, **kw):
        return {"on": True, "mode": "rgb", "rgb": (77, 13, 0), "white": 0, "brightness": 30, **kw}

    def test_the_light_storing_the_scaled_colour_counts_as_applied(self):
        # (255,43,0) at 30% reads back as (77,13,0) on this firmware
        self.assertTrue(zb._matches(self.state(), power=True, brightness=30, rgb=(255, 43, 0)))

    def test_a_few_units_of_hue_rounding_are_tolerated_but_a_wrong_colour_is_not(self):
        self.assertTrue(zb._matches(self.state(rgb=(80, 10, 2)), brightness=30, rgb=(255, 43, 0)))
        self.assertFalse(zb._matches(self.state(rgb=(0, 0, 77)), brightness=30, rgb=(255, 43, 0)))
        self.assertFalse(zb._matches(self.state(mode="white"), brightness=30, rgb=(255, 43, 0)))

    def test_brightness_alone_judges_only_the_level(self):
        self.assertTrue(zb._matches(self.state(brightness=41), brightness=40))
        self.assertFalse(zb._matches(self.state(brightness=70), brightness=40))

    def test_off_is_judged_on_power_alone(self):
        self.assertTrue(zb._matches(self.state(on=False, rgb=(1, 2, 3)), power=False))
        self.assertFalse(zb._matches(self.state(on=True), power=False))

    def test_white_needs_white_mode_at_the_level(self):
        self.assertTrue(zb._matches(self.state(mode="white", brightness=60), brightness=60, white=True))
        self.assertFalse(zb._matches(self.state(mode="rgb", brightness=60), brightness=60, white=True))

    def test_no_reply_never_counts_as_applied(self):
        self.assertFalse(zb._matches(None, power=True))


class FakeLight:
    """Stands in for Session: records what was sent, answers queries from a scripted state."""
    instances = []

    def __init__(self, name):
        self.name, self.address, self.sent = name, "FAKE-UUID", []
        self.states = list(FakeLight.script)
        FakeLight.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def send(self, cmd, expect_reply=False):
        self.sent.append(bytes(cmd))

    async def query(self):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


def rgb_state(rgb, brightness, on=True):
    return {"on": on, "mode": "rgb", "rgb": rgb, "white": 0, "brightness": brightness}


class Apply(unittest.TestCase):
    def setUp(self):
        FakeLight.instances = []
        self.p = patch.object(zb, "_session", FakeLight)
        self.p.start()
        patch.object(zb, "SETTLE_SECONDS", 0).start()
        self.addCleanup(patch.stopall)

    def test_a_colour_change_is_one_command_carrying_the_brightness(self):
        FakeLight.script = [rgb_state((149, 255, 0), 100), rgb_state((77, 13, 0), 30)]
        done = zb.apply("L", rgb=(255, 43, 0), brightness=30)
        sent = FakeLight.instances[0].sent
        colour = [c for c in sent if c[:2] == bytes([0x3B, 0xA1])]
        self.assertEqual(len(colour), 1)
        self.assertEqual(colour[0][4], 30)
        # never a separate brightness command after a colour: it rescales the OLD colour
        self.assertFalse(any(c[:2] == bytes([0x3B, 0x01]) for c in sent))
        self.assertTrue(done["verified"])
        self.assertEqual(done["attempts"], 1)

    def test_a_colour_without_a_brightness_keeps_the_lights_current_level(self):
        FakeLight.script = [rgb_state((77, 13, 0), 30), rgb_state((0, 0, 77), 30)]
        done = zb.apply("L", rgb=(0, 0, 255))
        colour = [c for c in FakeLight.instances[0].sent if c[:2] == bytes([0x3B, 0xA1])]
        self.assertEqual(colour[0][4], 30)
        self.assertTrue(done["verified"])

    def test_a_light_that_disagrees_is_retried_once_then_reported_not_faked(self):
        FakeLight.script = [rgb_state((149, 255, 0), 100), rgb_state((149, 255, 0), 100)]
        done = zb.apply("L", rgb=(0, 0, 255), brightness=100)
        self.assertFalse(done["verified"])
        self.assertEqual(done["attempts"], 2)
        self.assertEqual(done["state"]["rgb"], (149, 255, 0))

    def test_on_then_colour_sends_power_first(self):
        FakeLight.script = [rgb_state((0, 0, 0), 0, on=False), rgb_state((0, 0, 255), 100)]
        done = zb.apply("L", power=True, rgb=(0, 0, 255), brightness=100)
        sent = FakeLight.instances[0].sent
        self.assertEqual(sent[0][:2], bytes([0x3B, 0x23]))   # the fake answers queries without send()
        self.assertEqual(sent[1][:2], bytes([0x3B, 0xA1]))
        self.assertTrue(done["verified"])

    def test_off_is_confirmed_by_power_alone(self):
        FakeLight.script = [rgb_state((77, 13, 0), 30), rgb_state((77, 13, 0), 30, on=False)]
        done = zb.apply("L", power=False)
        self.assertTrue(done["verified"])
        self.assertEqual(FakeLight.instances[0].sent, [zb.cmd_power(False)])

    def test_an_unreachable_light_raises_instead_of_reporting_anything(self):
        class Gone(FakeLight):
            async def __aenter__(self):
                raise zb.BleUnavailable("not advertising")
        patch.object(zb, "_session", Gone).start()
        with self.assertRaises(zb.BleUnavailable):
            zb.apply("L", power=True)


class Helper(unittest.TestCase):
    """The launchd helper: it is what Archibald's scripts reach the radio through."""

    def setUp(self):
        FakeLight.instances = []
        patch.object(zb, "_session", FakeLight).start()
        patch.object(zb, "SETTLE_SECONDS", 0).start()
        self.addCleanup(patch.stopall)
        # a real helper on a real socket, in the test sandbox, serving the fake light
        import asyncio
        import threading
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        async def run():
            sock = zb.HELPER_SOCKET
            sock.parent.mkdir(parents=True, exist_ok=True)
            if sock.exists():
                sock.unlink()
            self.server = await asyncio.start_unix_server(helper._handle, path=str(sock))
            ready.set()
            try:
                async with self.server:
                    await self.server.serve_forever()
            except asyncio.CancelledError:
                pass  # the cleanup closed the server; that is the normal way out

        self.thread = threading.Thread(target=lambda: self.loop.run_until_complete(run()), daemon=True)
        self.thread.start()
        self.assertTrue(ready.wait(5))
        self.addCleanup(self.loop.call_soon_threadsafe, self.server.close)

    def test_status_and_apply_go_through_the_helper_and_come_back_whole(self):
        FakeLight.script = [rgb_state((149, 255, 0), 100), rgb_state((77, 13, 0), 30)]
        st = zb.status("L")
        self.assertEqual((st["on"], st["rgb"]), (True, (149, 255, 0)))
        done = zb.apply("L", rgb=(255, 43, 0), brightness=30)
        self.assertTrue(done["verified"])
        self.assertEqual(done["state"]["rgb"], (77, 13, 0))
        # the radio work happened in the helper's loop, not in this process's own asyncio.run
        self.assertEqual(len(FakeLight.instances), 2)

    def test_an_unreachable_light_is_still_a_clear_error_through_the_helper(self):
        class Gone(FakeLight):
            async def __aenter__(self):
                raise zb.BleUnavailable("not advertising")
        patch.object(zb, "_session", Gone).start()
        with self.assertRaisesRegex(zb.BleUnavailable, "not advertising"):
            zb.status("L")

    def test_ping_and_a_bad_op(self):
        import asyncio
        self.assertEqual(asyncio.run(helper.serve_request({"op": "ping"})), {"ok": True, "result": "pong"})
        self.assertFalse(asyncio.run(helper.serve_request({"op": "dance", "name": "L"}))["ok"])


class NoHelper(unittest.TestCase):
    def test_with_no_helper_listening_the_radio_is_used_directly(self):
        FakeLight.instances = []
        patch.object(zb, "_session", FakeLight).start()
        patch.object(zb, "SETTLE_SECONDS", 0).start()
        patch.object(zb, "HELPER_SOCKET", Path("/tmp/zengge-nobody-listening.sock")).start()
        self.addCleanup(patch.stopall)
        FakeLight.script = [rgb_state((1, 2, 3), 50)]
        self.assertEqual(zb.status("L")["rgb"], (1, 2, 3))
        self.assertEqual(len(FakeLight.instances), 1)


class Renditions(unittest.TestCase):
    """The Govee Sunset Gold washes out on these sunset-lamp projectors; the user tuned a match live
    on 2026-10-12. The jobs keep sending the Govee value; the lamps translate it."""

    def setUp(self):
        import json as _json
        cfg = Path(os.environ["OPENCLAW_HOME"]) / "workspace/Skills/zengge-lights/config/zengge_devices.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(_json.dumps({"devices": {"living_room": {"name": "L"}},
                                    "renditions": {"255,170,90": {"color": "255,125,15", "brightness_scale": 0.9}}}))

    def test_sunset_gold_becomes_the_tuned_amber_at_scaled_brightness(self):
        self.assertEqual(zb.render((255, 170, 90), 50), ((255, 125, 15), 45))
        self.assertEqual(zb.render((255, 170, 90), 6), ((255, 125, 15), 5))      # the night light
        self.assertEqual(zb.render((255, 170, 90), None), ((255, 125, 15), None))

    def test_any_other_colour_passes_through_untouched(self):
        self.assertEqual(zb.render((0, 255, 0), 50), ((0, 255, 0), 50))
        self.assertEqual(zb.render((255, 170, 91), 50), ((255, 170, 91), 50))

    def test_the_fade_sends_the_rendition_not_the_raw_gold(self):
        sent = []
        patch.object(fade, "apply", lambda name, **kw: sent.append(kw) or {"verified": True}).start()
        self.addCleanup(patch.stopall)
        fade.run_fade([("living_room", {"name": "L"})], 80, sleep=lambda s: None, clock=lambda: 0.0)
        self.assertEqual(sent[0]["rgb"], (255, 125, 15))
        self.assertEqual([k.get("brightness") for k in sent[:-1]], [round(b * 0.9) for b in fade.BRIGHTNESS_CURVE])


class EveningFade(unittest.TestCase):
    """The 10pm fade follows the Govee curve exactly and ends in off, whatever happens."""

    def setUp(self):
        self.calls = []
        self.slept = []

        def fake_apply(name, power=None, brightness=None, rgb=None, white=None, tries=2):
            self.calls.append({"power": power, "brightness": brightness, "rgb": rgb})
            return {"verified": True, "state": {}, "attempts": 1}
        patch.object(fade, "apply", fake_apply).start()
        self.addCleanup(patch.stopall)
        self.now = [0.0]

    def clock(self):
        return self.now[0]

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now[0] += seconds

    def test_it_is_the_govee_curve_colour_first_then_brightness_only_then_off(self):
        fade.run_fade([("living_room", {"name": "L"})], 2700, sleep=self.sleep, clock=self.clock)
        self.assertEqual([c["brightness"] for c in self.calls[:-1]], list(fade.BRIGHTNESS_CURVE))
        self.assertEqual(self.calls[0]["rgb"], fade.COLOR)          # colour set once, with step 1
        self.assertTrue(all(c["rgb"] is None for c in self.calls[1:-1]))   # then brightness alone
        self.assertEqual(self.calls[-1], {"power": False, "brightness": None, "rgb": None})
        # eight equal intervals: off lands at the full duration, not 1/8 early
        self.assertEqual(len(self.slept), len(fade.BRIGHTNESS_CURVE) + 1)
        self.assertAlmostEqual(sum(self.slept), 2700, places=2)
        self.assertAlmostEqual(self.slept[0], 2700 / 8, places=2)

    def test_time_spent_talking_to_the_light_comes_out_of_the_wait_not_added_to_it(self):
        real_apply = fade.apply

        def slow_apply(*a, **kw):
            self.now[0] += 4.0   # a Bluetooth round trip
            return real_apply(*a, **kw)
        patch.object(fade, "apply", slow_apply).start()
        fade.run_fade([("living_room", {"name": "L"})], 2700, sleep=self.sleep, clock=self.clock)
        self.assertAlmostEqual(self.now[0], 2700 + 4.0, places=2)   # only the final off runs past

    def test_an_unreachable_light_mid_fade_is_sent_straight_to_off(self):
        n = {"i": 0}

        def flaky(name, power=None, brightness=None, rgb=None, white=None, tries=2):
            n["i"] += 1
            if n["i"] == 3:
                raise zb.BleUnavailable("gone")
            self.calls.append({"power": power, "brightness": brightness})
            return {"verified": True, "state": {}, "attempts": 1}
        patch.object(fade, "apply", flaky).start()
        with self.assertRaises(zb.BleUnavailable):
            fade.run_fade([("living_room", {"name": "L"})], 2700, sleep=self.sleep, clock=self.clock)
        self.assertEqual(self.calls[-1]["power"], False)

    def test_misfire_rules_are_the_govee_ones(self):
        self.assertIs(fade.misfire_mode, sys.modules["winddown_fade"].misfire_mode)


if __name__ == "__main__":
    unittest.main(verbosity=1)
