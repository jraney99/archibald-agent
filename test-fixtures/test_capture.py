"""Check that fixture redaction preserves structure and rejects unknown fields."""
import json
from pathlib import Path
import tempfile
import unittest

from capture_tool_fixture import capture, shape


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "fixture.jsonl"
        self.fixtures = json.loads((Path(__file__).parent / "deployed-tool-calls.json").read_text())["fixtures"]

    def export(self, call):
        self.source.write_text(json.dumps({"message": {"content": [call]}}) + "\n")
        return capture(self.source, 1, call["name"])

    def test_shapes_and_redaction(self):
        for fixture in self.fixtures:
            with self.subTest(tool=fixture["toolCall"]["name"]):
                call = fixture["toolCall"]
                result = self.export(call)
                self.assertEqual(result["observedShape"], fixture["observedShape"])
                self.assertEqual(shape(result["toolCall"]), shape(call))
                self.assertEqual(result["toolCall"], call)

    def test_unknown_fields_require_review(self):
        for target in ("envelope", "arguments", "edit"):
            call = json.loads(json.dumps(next(f["toolCall"] for f in self.fixtures if f["toolCall"]["name"] == "edit")))
            field = call if target == "envelope" else call["arguments"] if target == "arguments" else call["arguments"]["edits"][0]
            field["new_private_field"] = "SECRET_SENTINEL"
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "review"):
                self.export(call)

    def test_private_values_removed(self):
        call = {"type": "toolCall", "id": "SECRET_SENTINEL", "name": "write",
                "arguments": {"path": "SECRET_SENTINEL", "content": "SECRET_SENTINEL"}}
        self.assertNotIn("SECRET_SENTINEL", json.dumps(self.export(call)))


if __name__ == "__main__":
    unittest.main()
