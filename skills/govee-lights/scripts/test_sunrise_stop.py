#!/usr/bin/env python3
"""Stopping a sunrise in progress (2026-10-01). The user: "during the sunrise I'll say stop and it
turns back on which is super annoying" — the ramp's next step relit the strip ~37s later.

What must hold:
  1. Any way of switching the light off ends the ramp — `--stop`, or plain `control.py --off`.
  2. The strip reporting itself off ends the ramp too (Govee app, plug pulled).
  3. A stop can only end the ramp it was asked during: yesterday's flag never cancels today's
     sunrise, and an unreachable strip is never read as "off" (better a sunrise than a dark
     bedroom on a guess).

No network: GoveeClient and the LAN read are stubbed.

    python3 test_sunrise_stop.py
"""
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS_DIR = Path(__file__).parent
DEV = {"sku": "H61E6", "device": "AA:BB:CC:DD:EE:FF:00:02"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sunrise_ramp = _load("sunrise_ramp", _SCRIPTS_DIR / "sunrise_ramp.py")
control = _load("control", _SCRIPTS_DIR / "control.py")


def _run(mod, argv):
    out = io.StringIO()
    code = 0
    with patch.object(sys, "argv", argv), redirect_stdout(out):
        try:
            mod.main()
        except SystemExit as e:
            code = e.code or 0
        except Exception:
            code = "raised"
    return code, out.getvalue()


class TempFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(sunrise_ramp, "STOP_FLAG", Path(self.tmp.name) / "sunrise_stop.json")
        p.start()
        self.addCleanup(p.stop)


class Flag(TempFlag):
    def test_a_stop_during_this_ramp_counts(self):
        started = time.time()
        self.assertIsNone(sunrise_ramp.stop_requested(started))
        sunrise_ramp.request_stop("asked to stop")
        self.assertEqual(sunrise_ramp.stop_requested(started)["reason"], "asked to stop")

    def test_an_older_stop_cannot_cancel_a_later_sunrise(self):
        sunrise_ramp.request_stop("yesterday")
        self.assertIsNone(sunrise_ramp.stop_requested(time.time() + 1))

    def test_clear_and_unreadable_flags_are_no_stop(self):
        sunrise_ramp.request_stop()
        sunrise_ramp.clear_stop()
        self.assertIsNone(sunrise_ramp.stop_requested(0))
        sunrise_ramp.STOP_FLAG.write_text("{not json")
        self.assertIsNone(sunrise_ramp.stop_requested(0))


class SwitchedOff(unittest.TestCase):
    def test_the_strip_saying_off_is_believed(self):
        with patch.dict(sys.modules, {"govee_lan": MagicMock(status=lambda d, ip=None: {"on": False})}):
            self.assertTrue(sunrise_ramp.switched_off(DEV["device"]))

    def test_the_strip_saying_on_keeps_the_ramp_going(self):
        with patch.dict(sys.modules, {"govee_lan": MagicMock(status=lambda d, ip=None: {"on": True})}):
            self.assertFalse(sunrise_ramp.switched_off(DEV["device"]))

    def test_no_local_answer_is_never_read_as_off(self):
        boom = MagicMock()
        boom.status.side_effect = OSError("no route to host")
        with patch.dict(sys.modules, {"govee_lan": boom}):
            self.assertFalse(sunrise_ramp.switched_off(DEV["device"]),
                             "an unreachable strip must not cancel the sunrise")


class RampEnds(TempFlag):
    """The ramp itself: 13 steps unless something says stop."""

    def ramp(self, stop_after=None, off_after=None):
        client = MagicMock()
        steps = []

        def apply_step(_c, _s, _d, brightness, rgb):
            steps.append(brightness)
            if stop_after is not None and len(steps) == stop_after:
                sunrise_ramp.request_stop("the light was switched off")
            return True

        with patch.object(sunrise_ramp, "load_device", return_value=DEV), \
             patch.object(sunrise_ramp, "GoveeClient", return_value=client), \
             patch.object(sunrise_ramp, "_power_on_with_grace", return_value=True), \
             patch.object(sunrise_ramp, "_apply_step", apply_step), \
             patch.object(sunrise_ramp, "switched_off",
                          lambda d: off_after is not None and len(steps) >= off_after), \
             patch.object(sunrise_ramp.time, "sleep"):
            code, out = _run(sunrise_ramp, ["sunrise_ramp.py", "--light", "bedroom", "--duration-seconds", "450"])
        return code, [json.loads(line) for line in out.strip().splitlines()], steps

    def test_a_whole_sunrise_runs_every_step(self):
        code, _, steps = self.ramp()
        self.assertEqual(code, 0)
        self.assertEqual(len(steps), len(sunrise_ramp.CURVE))

    def test_saying_stop_ends_it_at_the_next_step(self):
        code, lines, steps = self.ramp(stop_after=3)
        self.assertEqual(code, 0, "a stop the user asked for is not a failure")
        self.assertEqual(len(steps), 3, "no further step may relight the strip")
        self.assertEqual(lines[-1]["sunrise"], "stopped")
        self.assertEqual(lines[-1]["why"], "the light was switched off")
        self.assertFalse(sunrise_ramp.STOP_FLAG.exists(), "the flag is consumed, not left for tomorrow")

    def test_the_strip_being_switched_off_ends_it_too(self):
        code, lines, steps = self.ramp(off_after=2)
        self.assertEqual(code, 0)
        self.assertEqual(len(steps), 2)
        self.assertEqual(lines[-1]["why"], "the light was switched off")

    def test_a_stop_left_over_from_yesterday_is_cleared_at_the_start(self):
        sunrise_ramp.request_stop("yesterday")
        code, _, steps = self.ramp()
        self.assertEqual(len(steps), len(sunrise_ramp.CURVE), "today's sunrise must still run")


class StopCommand(TempFlag):
    def test_stop_switches_the_light_off_and_flags_the_ramp(self):
        client = MagicMock()
        with patch.object(sunrise_ramp, "load_device", return_value=DEV), \
             patch.object(sunrise_ramp, "GoveeClient", return_value=client):
            code, out = _run(sunrise_ramp, ["sunrise_ramp.py", "--light", "bedroom", "--stop"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["sunrise"], "stopped")
        client.power.assert_called_once_with(DEV["sku"], DEV["device"], False)
        self.assertIsNotNone(sunrise_ramp.stop_requested(0))

    def test_stop_still_ends_the_ramp_when_the_light_cannot_be_reached(self):
        client = MagicMock()
        client.power.side_effect = sunrise_ramp.GoveeError("Device is offline")
        with patch.object(sunrise_ramp, "load_device", return_value=DEV), \
             patch.object(sunrise_ramp, "GoveeClient", return_value=client):
            code, out = _run(sunrise_ramp, ["sunrise_ramp.py", "--light", "bedroom", "--stop"])
        self.assertEqual(code, 1)
        self.assertIn("power_off_failed", out)
        self.assertIsNotNone(sunrise_ramp.stop_requested(0), "the ramp must end even so")

    def test_turning_a_light_off_the_ordinary_way_also_stops_the_sunrise(self):
        with patch.object(control, "load_devices", return_value={"bedroom": DEV}), \
             patch.object(control, "GoveeClient", return_value=MagicMock()):
            code, _ = _run(control, ["control.py", "--light", "bedroom", "--off"])
        self.assertEqual(code, 0)
        self.assertIsNotNone(sunrise_ramp.stop_requested(0))

    def test_turning_a_light_on_does_not_write_a_stop(self):
        with patch.object(control, "load_devices", return_value={"bedroom": DEV}), \
             patch.object(control, "GoveeClient", return_value=MagicMock()):
            _run(control, ["control.py", "--light", "bedroom", "--on", "--brightness", "40"])
        self.assertIsNone(sunrise_ramp.stop_requested(0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
