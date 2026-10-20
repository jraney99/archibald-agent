import assert from "node:assert/strict";
import { inspectConstitutionWrite, inspectProcessInput } from "./index.mjs";

let pass = 0;
const t = (name, fn) => {
  try {
    fn();
    pass++;
    console.log(`  ok  ${name}`);
  } catch (e) {
    console.error(`FAIL  ${name}\n      ${e.message}`);
    process.exitCode = 1;
  }
};

// --- Writes to protected files -> block ---

for (const cmd of [
  `echo "new soul" > ~/.openclaw/workspace/SOUL.md`,
  `echo "new soul" >> workspace/SOUL.md`,
  `cat SOUL.md.new | tee ~/.openclaw/workspace/SOUL.md`,
  `cp draft.md ~/.openclaw/workspace/IDENTITY.md`,
  `mv draft.md ~/.openclaw/workspace/SECURITY.md`,
  `sed -i '' 's/old/new/' ~/.openclaw/workspace/SOUL.md`,
  `python3 -c "open('~/.openclaw/workspace/SOUL.md', 'w').write('x')"`,
  `python3 -c "from pathlib import Path; Path('workspace/SECURITY.md').write_text('x')"`,
]) {
  t(`block write: ${cmd.slice(0, 50)}...`, () => {
    const r = inspectConstitutionWrite(cmd);
    assert.ok(r?.block, "expected block");
    assert.match(r.blockReason, /propose|the user/i);
  });
}

// --- Deletion -> block ---

t("block rm on a protected file", () => {
  const r = inspectConstitutionWrite("rm ~/.openclaw/workspace/IDENTITY.md");
  assert.ok(r?.block);
});

t("block trash on a protected file", () => {
  const r = inspectConstitutionWrite("trash workspace/SECURITY.md");
  assert.ok(r?.block);
});

// --- Clearing the immutable flag -> block (the actual bypass path) ---

for (const cmd of [
  "chflags nouchg ~/.openclaw/workspace/SOUL.md",
  "chflags -uchg workspace/SECURITY.md",
]) {
  t(`block chflags unlock: ${cmd}`, () => {
    const r = inspectConstitutionWrite(cmd);
    assert.ok(r?.block, "expected block");
    assert.match(r.blockReason, /chflags/);
  });
}

// --- Hard Stop mechanism state files -> block direct tampering ---

for (const cmd of [
  `echo '{"x":{"status":"approved"}}' > ~/.openclaw/hard_stop_pending.json`,
  `python3 -c "import json; json.dump({}, open('~/.openclaw/yubikey_credential.json','w'))"`,
  `cp fake_creds.json ~/.openclaw/yubikey_credential.json`,
]) {
  t(`block guard-state tamper: ${cmd.slice(0, 50)}...`, () => {
    const r = inspectConstitutionWrite(cmd);
    assert.ok(r?.block, "expected block");
    assert.match(r.blockReason, /yubikey_auth\.py/);
  });
}

t("allow running yubikey_auth.py itself (never names its own state file)", () => {
  const r = inspectConstitutionWrite("python3 Skills/shared/yubikey_auth.py request id1 'test'");
  assert.equal(r, null);
});

t("allow reading hard_stop_pending.json", () => {
  const r = inspectConstitutionWrite("cat ~/.openclaw/hard_stop_pending.json");
  assert.equal(r, null);
});

// --- Disabling a guard plugin via openclaw.json -> block ---

for (const cmd of [
  `sed -i '' 's/"constitution-guard"//' ~/.openclaw/openclaw.json`,
  `python3 -c "import json; d=json.load(open('openclaw.json')); d['plugins']['entries']['email-send-guard']['enabled']=False; json.dump(d, open('openclaw.json','w'))"`,
]) {
  t(`block disabling a guard plugin: ${cmd.slice(0, 50)}...`, () => {
    const r = inspectConstitutionWrite(cmd);
    assert.ok(r?.block, "expected block");
  });
}

// --- Changing OpenClaw's own settings, by any route -> block ---

for (const cmd of [
  `sed -i '' 's/"perplexity"/"perplexity2"/' ~/.openclaw/openclaw.json`,
  `python3 -c "import json;p='~/.openclaw/openclaw.json';c=json.load(open(p));c['channels']['telegram']['accounts']['default']['allowFrom'].append('tg:999');json.dump(c,open(p,'w'))"`,
  `echo '{"agents":{"main":{"security":"full"}}}' > ~/.openclaw/exec-approvals.json`,
  `cp /tmp/evil.json ~/.openclaw/openclaw.json.last-good`,
  "openclaw plugins disable constitution-guard",
  "/opt/homebrew/bin/openclaw plugins disable email-send-guard",
  "node /opt/homebrew/lib/node_modules/openclaw/openclaw.mjs plugins uninstall callback-dispatch",
  "npx openclaw@latest plugins disable constitution-guard",
  "openclaw --log-level debug plugins disable constitution-guard",
  "openclaw plugins list disable constitution-guard",
  "openclaw plugins install ./evil-plugin",
  "openclaw config set plugins.entries.constitution-guard.enabled false",
  `openclaw config set channels.telegram.accounts.default.allowFrom '["tg:999"]'`,
  "openclaw config unset channels.telegram.accounts.default.allowFrom",
  "openclaw config patch --file /tmp/p.json",
  "openclaw config set tools.exec.ask off",
  "openclaw config",
  "openclaw approvals allowlist add '*'",
  "openclaw approvals set --file /tmp/a.json",
  "openclaw exec-policy set yolo",
  "openclaw pairing approve telegram ABC123",
  "openclaw devices approve --latest",
  "openclaw devices rotate --role operator",
  "openclaw channels add --channel telegram --token 1:abc",
  "openclaw channels logout telegram",
  "openclaw agents bind --agent demo --bind telegram:default",
  "openclaw hooks disable some-hook",
  "openclaw mcp add evil -- npx evil-mcp",
  "openclaw mcp tools brokerage --clear",
  "openclaw mcp configure brokerage --enable",
  "openclaw mcp login brokerage --code abc",
  "openclaw plugins disable trading-guard",
  "openclaw skills install clawhub:evil",
  "openclaw secrets apply --file /tmp/s.json",
  "openclaw models set openai/gpt-4o",
  "openclaw gateway call config.patch --params '{}'",
  "openclaw gateway install --force",
  "openclaw daemon uninstall",
  "openclaw update",
  "openclaw doctor --fix",
  "openclaw doctor --repair --yes",
  "openclaw security audit --fix",
  "openclaw configure --section channels",
  "openclaw onboard --non-interactive",
  "openclaw reset --yes",
  "cd ~ && openclaw plugins disable constitution-guard",
  "echo ok; openclaw plugins disable constitution-guard",
  "$(openclaw plugins disable constitution-guard)",
  `openclaw cron add --name x --cron "* * * * *" --command-argv '["openclaw","plugins","disable","constitution-guard"]'`,
  `openclaw cron add --name x --command "openclaw config set tools.exec.ask off"`,
  "echo 123:abc | python3 ~/.openclaw/scripts/rotate_secret.py TELEGRAM_BOT_TOKEN",
]) {
  t(`block settings change: ${cmd.slice(0, 60)}`, () => {
    const r = inspectConstitutionWrite(cmd);
    assert.ok(r?.block, "expected block");
    assert.match(r.blockReason, /Master Wayne|Propose/);
  });
}

// --- Guard code and data, via the shell -> block ---

for (const cmd of [
  "sed -i '' 's/block: true/block: false/' ~/.openclaw/plugins/outbound-guard/index.mjs",
  "echo '{\"addresses\":[\"x@evil.example\"]}' > ~/.openclaw/plugins/outbound-guard/known_invitees.json",
  "cd ~/.openclaw/plugins/outbound-guard && python3 -c \"open('known_invitees.json','w').write('{}')\"",
  "rm -rf ~/.openclaw/plugins/email-send-guard",
  "cp /tmp/x.mjs ~/.openclaw/plugins/memory-health-guard/index.mjs",
  "sed -i '' 's/new Set(\\[\\])/new Set([\"place_order\"])/' ~/.openclaw/plugins/trading-guard/index.mjs",
  "echo '{\"jobs\":[\"evil\"]}' > trading_jobs.json",
]) {
  t(`block guard-dir write: ${cmd.slice(0, 60)}`, () => {
    assert.ok(inspectConstitutionWrite(cmd)?.block, "expected block");
  });
}
t("allow reading guard code", () => {
  assert.equal(inspectConstitutionWrite("cat ~/.openclaw/plugins/outbound-guard/known_invitees.json"), null);
  assert.equal(inspectConstitutionWrite("grep -n EGRESS ~/.openclaw/plugins/outbound-guard/index.mjs | head"), null);
});

// --- Reading settings, and ordinary openclaw use -> allowed ---

for (const cmd of [
  "cat ~/.openclaw/openclaw.json",
  "cat ~/.openclaw/openclaw.json 2>/dev/null | python3 -m json.tool",
  "grep -n allowFrom ~/.openclaw/openclaw.json 2>&1 | head",
  `python3 - <<'PY'\nimport json\np='~/.openclaw/openclaw.json'\nwith open(p) as f: cfg=json.load(f)\nprint(cfg['agents']['defaults']['model'])\nPY`,
  "openclaw config get agents.defaults.model",
  "openclaw config validate",
  "openclaw plugins list",
  "openclaw plugins inspect callback-dispatch --runtime --json",
  "openclaw approvals get",
  "openclaw approvals --help",
  "openclaw plugins disable --help",
  "openclaw devices list",
  "openclaw channels status",
  "openclaw models status",
  "openclaw gateway status",
  "openclaw doctor --lint",
  "openclaw security audit",
  "openclaw cron list",
  `openclaw cron add --name hydration-nudge --cron "15 10 * * *" --tz America/Chicago --session isolated --command-argv '["python3","~/.openclaw/workspace/Skills/hydration/scripts/send_hydration_reminder.py"]' --no-deliver`,
  "openclaw cron edit abc --cron '0 9 * * *'",
  "openclaw browser fill --ref 12 --text hello",
  "openclaw message send --channel telegram --target 1 --message 'setup is done'",
  "ls -la ~/.openclaw/plugins",
  "tail -50 /tmp/openclaw/openclaw-2026-09-16.log",
  "python3 ~/.openclaw/workspace/Skills/shared/runtime_secrets.py",
]) {
  t(`allow: ${cmd.slice(0, 60)}`, () => {
    assert.equal(inspectConstitutionWrite(cmd), null);
  });
}

// --- Typing into a running shell with the process tool -> same checks ---

for (const params of [
  { action: "write", sessionId: "s1", data: "openclaw plugins disable constitution-guard\n" },
  { action: "paste", sessionId: "s1", text: "openclaw config set tools.exec.ask off" },
  { action: "send-keys", sessionId: "s1", literal: "echo x > ~/.openclaw/openclaw.json" },
  { action: "send-keys", sessionId: "s1", keys: [..."openclaw pairing approve telegram X", "Enter"] },
  { action: "send-keys", sessionId: "s1",
    hex: [..."openclaw devices approve --latest"].map((c) => c.charCodeAt(0).toString(16)) },
]) {
  t(`block process input: ${params.action} ${JSON.stringify(params).slice(40, 80)}`, () => {
    assert.ok(inspectProcessInput(params)?.block, "expected block");
  });
}

for (const params of [
  { action: "write", sessionId: "s1", data: "y\n" },
  { action: "poll", sessionId: "s1" },
  { action: "send-keys", sessionId: "s1", keys: ["C-c"] },
]) {
  t(`allow process input: ${JSON.stringify(params)}`, () => {
    assert.equal(inspectProcessInput(params), null);
  });
}

// --- Reads -> NOT blocked ---

for (const cmd of [
  "cat ~/.openclaw/workspace/SOUL.md",
  "grep -n 'Loyal' workspace/SOUL.md",
  "head -20 workspace/SECURITY.md",
  "wc -l workspace/IDENTITY.md",
]) {
  t(`allow read: ${cmd}`, () => {
    const r = inspectConstitutionWrite(cmd);
    assert.equal(r, null);
  });
}

// --- Unrelated commands / unrelated file writes -> NOT blocked ---

for (const cmd of [
  "echo 'note' >> workspace/RULES.md",
  "echo 'update' >> workspace/MEMORY.md",
  "ls -la workspace",
  "python3 Skills/shared/yubikey_auth.py pending",
]) {
  t(`allow unrelated: ${cmd}`, () => {
    const r = inspectConstitutionWrite(cmd);
    assert.equal(r, null);
  });
}

t("empty/undefined command -> no crash, no block", () => {
  assert.equal(inspectConstitutionWrite(""), null);
  assert.equal(inspectConstitutionWrite(undefined), null);
});

console.log(`\n${pass} passed`);
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";
await runRuntimeFixtures("constitution-guard");
