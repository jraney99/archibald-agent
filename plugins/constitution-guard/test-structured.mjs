import assert from "node:assert/strict";
import plugin from "./index.mjs";
import { isOwnerMessage } from "../callback-dispatch/owner.mjs";

let hook;
plugin.register({ on: (name, fn) => { hook = fn; }, logger: { info() {}, warn() {} } });
for (const event of [
  { toolName: "write", params: { path: "/tmp/SOUL.md", content: "new" } },
  { toolName: "edit", params: { path: "/tmp/hard_stop_pending.json", edits: [{ oldText: "pending", newText: "approved" }] } },
  { toolName: "write", params: { path: "/tmp/openclaw.json", content: "{}" } },
  { toolName: "apply_patch", params: { input: "*** Begin Patch\n*** Delete File: /tmp/SECURITY.md\n*** End Patch" } },
  { toolName: "edit", params: { file_path: "/tmp/IDENTITY.md", new_string: "new" } },
  { toolName: "write", params: { path: "/tmp/plugins/email-send-guard/index.mjs", content: "" } },
  { toolName: "edit", params: { path: "~/.openclaw/exec-approvals.json", new_string: "{}" } },
  { toolName: "process", params: { action: "write", sessionId: "s", data: "openclaw plugins disable constitution-guard\n" } },
  { toolName: "exec", params: { command: "openclaw config set tools.exec.ask off" } },
]) assert.equal((await hook(event)).block, true, JSON.stringify(event));
assert.equal(await hook({ toolName: "write", params: { path: "/tmp/notes.md", content: "note" } }), null);
assert.equal(await hook({ toolName: "process", params: { action: "write", sessionId: "s", data: "y\n" } }), null);
assert.equal(await hook({ toolName: "exec", params: { command: "openclaw cron list" } }), undefined);
const cfg = { channels: { telegram: { allowFrom: ["tg:123"] } } };
assert.equal(isOwnerMessage({ channel: "telegram", senderId: "123" }, { accountId: "default" }, cfg), true);
assert.equal(isOwnerMessage({ channel: "telegram", senderId: "123", isGroup: true }, {}, cfg), false);
assert.equal(isOwnerMessage({ channel: "telegram", senderId: "456" }, {}, cfg), false);
assert.equal(isOwnerMessage({ channel: "telegram", senderId: "123" }, { accountId: "demo" }, cfg), false);
console.log("16 structured guard and owner-scope checks passed");
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";
await runRuntimeFixtures("constitution-guard");
