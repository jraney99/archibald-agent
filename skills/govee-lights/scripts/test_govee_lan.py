#!/usr/bin/env python3
"""Local control (govee_lan.py, 2026-10-01) and the cloud fallback around it.

What matters here: a flat command is CONFIRMED against the strip itself; a strip that
disagrees is reported, never reported as success; and anything local control can't do falls
through to the cloud instead of failing. No sockets are opened — send/status are stubbed.

    python3 test_govee_lan.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SCRIPTS_DIR = Path(__file__).parent
DEV = "AA:BB:CC:DD:EE:FF:00:01"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


govee_lan = _load("govee_lan", _SCRIPTS_DIR / "govee_lan.py")
govee_client = _load("govee_client", _SCRIPTS_DIR / "govee_client.py")


class Wire(unittest.TestCase):
    def test_commands_are_the_shapes_goveees_lan_api_documents(self):
        self.assertEqual(json.loads(govee_lan._msg("turn", {"value": 1})),
                         {"msg": {"cmd": "turn", "data": {"value": 1}}})
        self.assertEqual(json.loads(govee_lan._msg("devStatus", {})),
                         {"msg": {"cmd": "devStatus", "data": {}}})

    def test_a_reply_that_is_not_json_is_ignored_not_raised(self):
        self.assertIsNone(govee_lan._parse(b"<html>nope"))
        self.assertEqual(govee_lan._parse(b'{"msg":{"cmd":"scan","data":{"device":"x"}}}'), {"device": "x"})


class Matching(unittest.TestCase):
    def state(self, **kw):
        return {"on": True, "brightness": 12, "rgb": (255, 140, 60), "kelvin": 0, **kw}

    def test_the_strip_agreeing_counts_as_applied(self):
        self.assertTrue(govee_lan._matches(self.state(), True, 12, (255, 140, 60), None))

    def test_any_disagreement_counts_as_not_applied(self):
        self.assertFalse(govee_lan._matches(self.state(brightness=50), None, 12, None, None))
        self.assertFalse(govee_lan._matches(self.state(rgb=(0, 0, 255)), None, None, (255, 140, 60), None))
        self.assertFalse(govee_lan._matches(self.state(on=False), True, None, None, None))
        self.assertFalse(govee_lan._matches(self.state(kelvin=4000), None, None, None, 2700))

    def test_fields_we_did_not_ask_about_are_not_judged(self):
        self.assertTrue(govee_lan._matches(self.state(rgb=(1, 2, 3), kelvin=9000), None, 12, None, None))


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(govee_lan, "CACHE", Path(self.tmp.name) / "govee_lan.json")
        p.start()
        self.addCleanup(p.stop)

    def test_a_known_ip_is_reused_without_a_scan(self):
        govee_lan._save_cache({DEV: "192.168.1.20"})
        with patch.object(govee_lan, "scan", side_effect=AssertionError("should not re-scan")):
            self.assertEqual(govee_lan.ip_for(DEV), "192.168.1.20")

    def test_an_unknown_device_scans_and_a_silent_one_is_not_local(self):
        with patch.object(govee_lan, "scan", return_value={DEV: "192.168.1.20"}):
            self.assertEqual(govee_lan.ip_for(DEV), "192.168.1.20")
        govee_lan.CACHE.unlink(missing_ok=True)  # patched scan() never writes the cache itself
        with patch.object(govee_lan, "scan", return_value={}):
            with self.assertRaises(govee_lan.LanUnavailable):
                govee_lan.ip_for("not-a-device")

    def test_a_stale_cache_is_rescanned_because_dhcp_moves_ips(self):
        govee_lan.CACHE.write_text(json.dumps({"at": 0, "ips": {DEV: "192.168.1.99"}}))
        with patch.object(govee_lan, "scan", return_value={DEV: "192.168.1.20"}) as scan:
            self.assertEqual(govee_lan.ip_for(DEV), "192.168.1.20")
        scan.assert_called_once()


class Apply(unittest.TestCase):
    def setUp(self):
        p = patch.object(govee_lan, "ip_for", return_value="192.168.1.20")
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(govee_lan, "SETTLE_SECONDS", 0)
        p2.start()
        self.addCleanup(p2.stop)

    def test_one_pass_when_the_strip_confirms(self):
        good = {"on": True, "brightness": 12, "rgb": (255, 140, 60), "kelvin": 0}
        with patch.object(govee_lan, "send") as send, patch.object(govee_lan, "status", return_value=good):
            out = govee_lan.apply(DEV, power=True, brightness=12, rgb=(255, 140, 60))
        self.assertEqual((out["verified"], out["attempts"], out["transport"]), (True, 1, "lan"))
        send.assert_called_once()

    def test_it_retries_once_then_reports_the_disagreement(self):
        wrong = {"on": True, "brightness": 50, "rgb": (0, 0, 255), "kelvin": 0}
        with patch.object(govee_lan, "send") as send, patch.object(govee_lan, "status", return_value=wrong):
            out = govee_lan.apply(DEV, brightness=12, rgb=(255, 140, 60))
        self.assertFalse(out["verified"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(out["state"], wrong)


class ClientFallback(unittest.TestCase):
    """GoveeClient tries local control first, then the cloud — and never claims a false success."""

    def setUp(self):
        self.client = govee_client.GoveeClient(api_key="test-key")

    def test_a_confirmed_local_command_never_touches_the_cloud(self):
        done = {"transport": "lan", "ip": "192.168.1.20", "verified": True, "state": {}}
        with patch.object(govee_lan, "apply", return_value=done), \
             patch.object(self.client, "control", side_effect=AssertionError("went to the cloud")):
            self.assertEqual(self.client.brightness("H61E6", DEV, 12), done)

    def test_no_local_control_falls_back_to_the_cloud(self):
        with patch.object(govee_lan, "apply", side_effect=govee_lan.LanUnavailable("off the network")), \
             patch.object(self.client, "control", return_value={"code": 200}) as cloud:
            self.assertEqual(self.client.color_rgb("H61E6", DEV, 255, 140, 60), {"code": 200})
        cloud.assert_called_once()

    def test_a_strip_that_disagrees_is_an_error_not_a_shrug(self):
        done = {"transport": "lan", "ip": "192.168.1.20", "verified": False,
                "state": {"on": True, "brightness": 50, "rgb": (0, 0, 255), "kelvin": 0}}
        with patch.object(govee_lan, "apply", return_value=done):
            with self.assertRaises(govee_client.GoveeError) as caught:
                self.client.color_rgb("H61E6", DEV, 255, 140, 60)
        self.assertIn("did not take", str(caught.exception))

    def test_govee_no_lan_forces_the_cloud(self):
        with patch.dict("os.environ", {"GOVEE_NO_LAN": "1"}), \
             patch.object(govee_lan, "apply", side_effect=AssertionError("used the LAN")), \
             patch.object(self.client, "control", return_value={"code": 200}):
            self.assertEqual(self.client.power("H61E6", DEV, True), {"code": 200})

    def test_scenes_stay_on_the_cloud_because_the_lan_api_has_no_scene_command(self):
        with patch.object(govee_lan, "apply", side_effect=AssertionError("used the LAN")), \
             patch.object(self.client, "control", return_value={"code": 200}) as cloud:
            self.client.scene("H61E6", DEV, {"id": 1, "paramId": 2})
            self.client.gradient_toggle("H61E6", DEV, False)
        self.assertEqual(cloud.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
