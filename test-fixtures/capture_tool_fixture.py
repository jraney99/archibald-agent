"""Export one sanitized, shape-preserving tool call for manual fixture review."""
import argparse
import hashlib
import json
from pathlib import Path


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    if value is None:
        return "null"
    return type(value).__name__


def capture(source, line_number, tool):
    with source.open() as stream:
        line = next(line for i, line in enumerate(stream, 1) if i == line_number)
    record = json.loads(line)
    calls = [b for b in record["message"]["content"] if b.get("type") == "toolCall" and b.get("name") == tool]
    if len(calls) != 1:
        raise ValueError("Select a line containing exactly one matching tool call")
    original = calls[0]
    call = json.loads(json.dumps(original))
    if set(call) != {"type", "id", "name", "arguments"}:
        raise ValueError("New envelope fields require explicit sanitizer review")
    call["id"] = "sanitized-" + tool
    args = call["arguments"]
    expected = {"exec": {"command"}, "write": {"path", "content"}, "edit": {"path", "edits"}, "apply_patch": {"input"}}
    if set(args) != expected[tool]:
        raise ValueError("New argument fields require explicit sanitizer review")
    if tool == "exec":
        args["command"] = "printf fixture"
    elif tool == "write":
        args.update(path="/fixture/workspace/notes.md", content="## Fixture\nSanitized text.\n")
    elif tool == "edit":
        args["path"] = "/fixture/workspace/notes.md"
        for edit in args["edits"]:
            if set(edit) != {"oldText", "newText"}:
                raise ValueError("New edit fields require explicit sanitizer review")
            edit.update(oldText="Sanitized old text.", newText="Sanitized new text.")
    else:
        args["input"] = "*** Begin Patch\n*** Update File: /fixture/workspace/notes.md\n@@\n-old\n+new\n*** End Patch"
    if shape(call) != shape(original):
        raise ValueError("Sanitization altered the captured schema")
    return {"id": "deployed-" + tool, "source": {"file": source.name, "line": line_number,
            "timestamp": record.get("timestamp"), "toolCallSha256": hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest(),
            "redactions": "IDs, paths, command and document text replaced; keys, scalar types and array cardinality preserved."},
            "observedShape": shape(original), "toolCall": call}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--line", type=int, required=True)
    parser.add_argument("--tool", choices=["exec", "edit", "write", "apply_patch"], required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.source, args.line, args.tool), indent=2))
