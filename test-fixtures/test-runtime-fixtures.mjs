import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runRuntimeFixtures, shape } from "./runtime-fixtures.mjs";

const source = readFileSync(new URL("../plugins/memory-health-guard/index.mjs", import.meta.url), "utf8");
const current = "inspectToolWrite(toolName, event?.params)";
assert.ok(source.includes(current), "Mutation target changed; review this regression test");
// Simulate the old internal-only argument contract in an in-memory module.
// No production file is edited, and effect tripwires remain active.
const legacy = source
  .replace(current, "inspectMemoryHealthWrite(event?.params ?? {})")
  // A data: module has no hierarchical parent URL. Replace only the build-stamp
  // import with inert fixture helpers; the guard under test stays unchanged.
  .replace(
    'import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";',
    'const pluginBuildStamp = () => ({ hash: "fixture", files: [] });\nconst logPluginBuildStamp = () => {};',
  );
await assert.rejects(
  runRuntimeFixtures("memory-health-guard", { pluginURL: `data:text/javascript;base64,${Buffer.from(legacy).toString("base64")}` }),
  /expected a block/,
);
assert.notDeepEqual(shape({ path: "x", edits: [{ oldText: "a", newText: "b" }] }),
  shape({ file_path: "x", old_string: "a", new_string: "b" }));
console.log("Fixture harness rejects the legacy memory schema and argument normalization.");
