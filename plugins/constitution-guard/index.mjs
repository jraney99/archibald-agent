import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * constitution-guard — deterministic guard against Archibald editing his own
 * governing files, tampering with the Hard Stop mechanism's own state, or
 * disabling any guard plugin (including this one) through config.
 *
 * WHY THIS EXISTS
 * SOUL.md, IDENTITY.md, and SECURITY.md say Archibald doesn't edit himself —
 * he proposes changes to the user in conversation and the user applies them. That's a
 * prose norm a drifted or literal-minded agent can rationalize past ("this
 * edit is clearly helpful, the user would want it"). This plugin makes it a rule
 * instead of a request: it blocks the underlying `exec` command before it
 * runs, the same way calendar-write-guard and email-send-guard turn a
 * RULES.md paragraph into code.
 *
 * Three things are protected, because protecting only the first one leaves
 * the other two as a way to defeat it entirely:
 *
 *   1. The constitution itself — SOUL.md, IDENTITY.md, SECURITY.md.
 *   2. The Hard Stop mechanism's own state — hard_stop_pending.json,
 *      yubikey_credential.json (Skills/shared/yubikey_auth.py's files). A
 *      Hard Stop is worthless if Archibald can just `echo` a fake "approved"
 *      status into hard_stop_pending.json, or overwrite the enrolled public
 *      key in yubikey_credential.json with one he controls, instead of
 *      actually waiting on the user's key touch. Found 2026-09-12 during a gap
 *      review of the Hard Stop list — this was a bigger hole than any
 *      missing category, since it defeats all seven at once. The legitimate
 *      script's own internal writes to these files are invisible to this
 *      check (they happen inside the process, never naming the file on the
 *      command line) — only a direct ad-hoc override is caught.
 *   3. OpenClaw's own settings — openclaw.json, exec-approvals.json, and the
 *      openclaw CLI commands that change config, plugins, command approvals,
 *      channels, pairing and devices. Otherwise Archibald could neutralize
 *      every guard (`openclaw plugins disable constitution-guard`) or add a
 *      stranger to the Telegram allowFrom list. Widened 2026-09-16 from
 *      "edits naming a guard plugin id", which both of those slipped past.
 *
 * Defense in depth on (1), not a replacement for one: SOUL/IDENTITY/SECURITY
 * are ALSO set OS-level immutable (`chflags uchg`) as of 2026-09-12, so even
 * a write this plugin somehow missed still fails at the filesystem level.
 * (2) and (3) are NOT chflags-locked — the state files need to stay writable
 * for yubikey_auth.py's own normal operation, and openclaw.json is rewritten
 * by the gateway itself and by the user's own tools. This plugin only stops
 * Archibald's tool calls; the user changes settings himself.
 *
 * WHAT IT DOES (before_tool_call: exec, process input, write/edit/apply_patch)
 *   - A write/delete/unlock-like command mentioning SOUL.md, IDENTITY.md, or
 *     SECURITY.md -> BLOCK.
 *   - A write-like command mentioning hard_stop_pending.json or
 *     yubikey_credential.json -> BLOCK (run yubikey_auth.py's own
 *     subcommands instead — they never need the filename on the command
 *     line).
 *   - A write-like command mentioning openclaw.json or exec-approvals.json,
 *     any mention of rotate_secret.py, or an `openclaw` CLI call outside the
 *     read-only subcommands in SETTINGS_COMMANDS -> BLOCK. Nested calls count
 *     (`openclaw cron add --command-argv '["openclaw","plugins","disable",…]'`).
 *   - Text typed into a running session with the `process` tool gets the same
 *     checks, per call.
 *
 * LIMITS: pattern matching, not a sandbox. A deliberately disguised command
 * (base64-decoded, assembled from variables, or typed in pieces across
 * several `process` calls) can get past it. It stops the plain, planted
 * instruction — the realistic prompt-injection case.
 *
 * Deliberately NOT exempted: cron/job runs. Every other guard in this repo
 * skips `ctx.jobId` because cron-driven calendar/email writes are common and
 * legitimate. There is no legitimate cron job that should ever touch any of
 * the above, so a job-driven attempt is treated the same as an ad-hoc one —
 * if anything it's more suspicious, being unattended.
 *
 * WHAT IT LEAVES ALONE
 * Reads of any of the above, any command that doesn't mention one of them,
 * `python3 .../yubikey_auth.py <subcommand>` (never names its own state
 * files on the command line), read-only openclaw commands (`config get`,
 * `plugins list`, `cron ...`, `browser ...`, any `--help`), and writes to
 * every other file in the repo —
 * including RULES.md and MEMORY.md, which Archibald is meant to keep
 * maintaining himself.
 */

const PROTECTED_BASENAMES = ["SOUL.md", "IDENTITY.md", "SECURITY.md"];
const GUARD_STATE_BASENAMES = ["hard_stop_pending.json", "yubikey_credential.json"];
const GUARD_PLUGIN_IDS = [
  "constitution-guard",
  "calendar-write-guard",
  "calendar-read-guard",
  "email-send-guard",
  "callback-dispatch",
  "memory-health-guard",
  "outbound-guard",
  "trading-guard",
];
// Guard code and data (e.g. outbound-guard's known_invitees.json) live under
// plugins/<guard-id>/; a shell write there would take effect at the next restart.
const MENTIONS_GUARD_DIR_RE = new RegExp(`plugins/(${GUARD_PLUGIN_IDS.join("|")})\\b|\\bknown_invitees\\.json\\b|\\btrading_jobs\\.json\\b`);

const reFor = (names) => new RegExp(`\\b(${names.map((n) => n.replace(/\./g, "\\.")).join("|")})\\b`);

const MENTIONS_PROTECTED_RE = reFor(PROTECTED_BASENAMES);
const MENTIONS_GUARD_STATE_RE = reFor(GUARD_STATE_BASENAMES);
const MENTIONS_OPENCLAW_JSON_RE = /\bopenclaw\.json\b/;
const MENTIONS_GUARD_PLUGIN_ID_RE = reFor(GUARD_PLUGIN_IDS);
// OpenClaw's own settings and command-approval files, plus the local tool
// that swaps secrets inside them.
const MENTIONS_SETTINGS_FILE_RE = /\b(openclaw\.json|exec-approvals\.json)\b/;
const MENTIONS_SETTINGS_TOOL_RE = /\brotate_secret\.py\b/;

// OpenClaw CLI groups that can change who Archibald obeys, which plugins and
// guards run, or its own config (added 2026-09-16: `openclaw plugins disable
// constitution-guard` and `openclaw config set channels...allowFrom` both
// passed the file-edit checks). Only the read-only subcommands listed pass;
// anything else in the group — including subcommands a future OpenClaw adds —
// is blocked. `null` blocks the command outright. Archibald's history (~34k
// exec calls) had no legitimate use of any blocked form after first setup.
const SETTINGS_COMMANDS = {
  config: ["file", "get", "schema", "validate"],
  plugins: ["doctor", "inspect", "list", "marketplace", "search", "validate"],
  approvals: ["get"],
  "exec-approvals": ["get"],
  "exec-policy": ["show"],
  channels: ["capabilities", "list", "logs", "resolve", "status"],
  pairing: ["list"],
  devices: ["list"],
  nodes: ["describe", "list", "pending", "status"],
  hooks: ["check", "info", "list"],
  agents: ["bindings", "list"],
  secrets: ["audit"],
  // Not `tools`: it rewrites a server's tool filter (2026-09-21, brokerage MCP).
  mcp: ["doctor", "list", "probe", "show", "status"],
  skills: ["check", "info", "list", "search", "verify"],
  models: ["list", "status"],
  gateway: ["diagnostics", "discover", "health", "probe", "stability", "status", "usage-cost"],
  daemon: ["status"],
  update: ["status"],
  webhooks: [],
  configure: null,
  crestodian: null,
  migrate: null,
  onboard: null,
  reset: null,
  setup: null,
  uninstall: null,
};
// Groups that are read-only unless given a repair flag.
const REPAIR_FLAG_COMMANDS = new Set(["doctor", "security"]);
const REPAIR_FLAG_RE = /^--(fix|repair|yes|force|generate-gateway-token|allow-exec)(=|$)/;
// Every top-level command, so an option value (`--log-level debug`) is never
// mistaken for the command group.
const OPENCLAW_COMMANDS = new Set([
  ...Object.keys(SETTINGS_COMMANDS), ...REPAIR_FLAG_COMMANDS,
  "acp", "agent", "backup", "capability", "chat", "clawbot", "commitments", "completion", "cron",
  "dashboard", "directory", "dns", "docs", "health", "help", "infer", "logs", "memory", "message",
  "node", "proxy", "qr", "sandbox", "sessions", "status", "system", "tasks", "terminal",
  "transcripts", "tui", "browser",
]);
const OPENCLAW_BIN_RE = /(?:^|\/)openclaw(?:\.mjs)?(?:@\S*)?$/;

const SETTINGS_BLOCK_REASON =
  "You don't change OpenClaw's own settings — who you take orders from, which plugins and guards run, " +
  "command approvals, pairing, or the config file itself — by any route (openclaw CLI, editing openclaw.json " +
  "or exec-approvals.json, rotate_secret.py). That is SECURITY.md Hard Stop #4 territory, enforced here. " +
  "Tell Master Wayne exactly what change is needed and why; he makes it himself. Reading settings is fine.";

/** Each `openclaw ...` invocation in a command line, as its argument tokens. */
function openclawInvocations(cmd) {
  const invocations = [];
  // Quotes, JSON punctuation and `=` become spaces so a command nested in a
  // quoted string or a JSON argv (`["openclaw","plugins","disable",...]`) is
  // still seen token by token.
  const flat = cmd.replace(/["'`,[\]{}\\=]/g, " ");
  for (const segment of flat.split(/[;&|\n()]|\$\(/)) {
    let current = null;
    for (const token of segment.split(/\s+/).filter(Boolean)) {
      if (OPENCLAW_BIN_RE.test(token)) {
        current = [];
        invocations.push(current);
      } else if (current) {
        current.push(token);
      }
    }
  }
  return invocations;
}

function settingsCommandBlocked(args) {
  const at = args.findIndex((a) => !a.startsWith("-") && OPENCLAW_COMMANDS.has(a));
  if (at < 0) return false;
  const group = args[at];
  const rest = args.slice(at + 1);
  const words = rest.filter((a) => !a.startsWith("-"));
  if (rest.includes("--help") || rest.includes("-h") || words[0] === "help") return false;
  if (REPAIR_FLAG_COMMANDS.has(group)) return rest.some((a) => REPAIR_FLAG_RE.test(a));
  if (!(group in SETTINGS_COMMANDS)) return false;
  const allowed = SETTINGS_COMMANDS[group];
  if (allowed === null || words.length === 0 || !allowed.includes(words[0])) return true;
  // A read subcommand followed by a write one (`plugins list disable x`) is
  // not trusted to be a read.
  return words.slice(1).some((w) => WRITE_SUBCOMMAND_RE.test(w));
}
const WRITE_SUBCOMMAND_RE =
  /^(add|apply|approve|bind|clear|configure|delete|disable|enable|install|login|logout|patch|reject|reload|remove|rename|repair|reset|revoke|rotate|serve|set|set-identity|set-image|unbind|uninstall|unset|update|wizard|workshop|registry|init|build)$/;

function inspectSettingsChange(cmd) {
  if (MENTIONS_SETTINGS_TOOL_RE.test(cmd)) return { block: true, blockReason: SETTINGS_BLOCK_REASON };
  if (openclawInvocations(cmd).some(settingsCommandBlocked)) {
    return { block: true, blockReason: SETTINGS_BLOCK_REASON };
  }
  return null;
}

// Write/delete/unlock indicators. Deliberately broad within "mentions a
// protected file" — per GUARD-PATTERN.md a guard earns its keep on the
// narrow, high-frequency case, but under-catching here is a security gap,
// not a UX annoyance, so this list errs toward more patterns than the
// calendar guard's.
const WRITE_INDICATOR_RES = [
  />{1,2}/, // redirection: > or >>
  /\btee\b/,
  /\bcp\b/,
  /\bmv\b/,
  /\bsed\b[^\n]*-i/, // sed -i (in-place edit)
  /\brm\b/,
  /\btrash\b/,
  /\bchflags\b/, // covers both setting AND clearing uchg — clearing it is exactly the bypass to catch
  /\bopen\([^)]*['"]w/, // python open(..., 'w'/"w")
  /\.write\(/,
  /\.write_text\(/,
  /\btruncate\b/,
  /\bjq\b[^\n]*-i/, // jq --in-place style JSON edits, e.g. `jq -i` wrappers
];

function isWriteLike(cmd) {
  // Discarding or merging output (`2>/dev/null`, `2>&1`) is not a write.
  const withoutDiscards = cmd.replace(/\d?>{1,2}\s*(\/dev\/null\b|&\d)/g, " ");
  return WRITE_INDICATOR_RES.some((re) => re.test(withoutDiscards));
}

/**
 * @param {string} rawCmd
 * @returns {null | {block: true, blockReason: string}}
 */
export function inspectConstitutionWrite(rawCmd) {
  const cmd = String(rawCmd ?? "").trim();
  if (!cmd) return null;
  const settingsChange = inspectSettingsChange(cmd);
  if (settingsChange) return settingsChange;
  if (!isWriteLike(cmd)) return null; // a plain read (cat, grep, head, ...) — fine, whatever it mentions

  const constitutionMatch = MENTIONS_PROTECTED_RE.exec(cmd);
  if (constitutionMatch) {
    const file = constitutionMatch[1];
    return {
      block: true,
      blockReason:
        `You don't edit ${file} yourself, including via chflags/sed/redirection/cp/mv/rm — that's not your call to make, ` +
        `per SOUL.md's "No Stake in Your Own Continuation" and this file's write-guard. ` +
        `Propose the specific change to Master Wayne in conversation. If he agrees, he applies it directly or approves you ` +
        `applying it under his explicit direction — not on your own initiative, and never as a side effect of another task. ` +
        `${file} is also OS-protected (chflags uchg); clearing that flag is exactly the kind of workaround this exists to stop.`,
    };
  }

  const guardDirMatch = MENTIONS_GUARD_DIR_RE.exec(cmd);
  if (guardDirMatch) {
    return {
      block: true,
      blockReason:
        `You don't change guard plugin code or data (${guardDirMatch[0]}) — that would switch off a protection at the ` +
        `next restart. Propose the change to Master Wayne instead.`,
    };
  }

  const stateMatch = MENTIONS_GUARD_STATE_RE.exec(cmd);
  if (stateMatch) {
    return {
      block: true,
      blockReason:
        `You don't write ${stateMatch[1]} directly — it's Hard Stop state, and a direct edit is exactly the bypass ` +
        `(faking an "approved" status, or swapping in a key you control) the YubiKey confirmation exists to prevent. ` +
        `Use \`Skills/shared/yubikey_auth.py\`'s own subcommands (request/confirm/deny/check/enroll) — never touch this file yourself.`,
    };
  }

  if (MENTIONS_SETTINGS_FILE_RE.test(cmd)) {
    const pluginMatch = MENTIONS_OPENCLAW_JSON_RE.test(cmd) && MENTIONS_GUARD_PLUGIN_ID_RE.exec(cmd);
    if (pluginMatch) {
      return {
        block: true,
        blockReason:
          `You don't disable or reconfigure the "${pluginMatch[1]}" guard plugin's entry in openclaw.json — that's the same ` +
          `kind of self-edit this whole plugin exists to stop, just one level removed. Propose it to Master Wayne instead.`,
      };
    }
    return { block: true, blockReason: SETTINGS_BLOCK_REASON };
  }

  return null;
}

/** The text a `process` tool call would type into a running session. */
export function inspectProcessInput(params = {}) {
  const keys = Array.isArray(params.keys)
    ? params.keys.map((k) => (String(k).length === 1 ? k : " ")).join("")
    : "";
  const hex = Array.isArray(params.hex)
    ? params.hex.map((h) => {
        const code = parseInt(String(h), 16);
        return Number.isNaN(code) ? " " : String.fromCharCode(code);
      }).join("")
    : "";
  const typed = [params.data, params.text, params.literal, keys, hex]
    .filter((s) => typeof s === "string" && s)
    .join("\n");
  return inspectConstitutionWrite(typed);
}


export function inspectStructuredWrite(toolName, params = {}) {
  const paths = [];
  if (toolName === "write" || toolName === "edit") paths.push(params.path ?? params.file_path ?? "");
  if (toolName === "apply_patch") {
    const patch = params.input ?? params.patch ?? "";
    for (const match of patch.matchAll(/^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$/gm)) paths.push(match[1]);
  }
  for (const path of paths) {
    if (MENTIONS_PROTECTED_RE.test(path) || MENTIONS_GUARD_STATE_RE.test(path) ||
        MENTIONS_SETTINGS_FILE_RE.test(path) ||
        (/plugins[/\\]/.test(path) && MENTIONS_GUARD_PLUGIN_ID_RE.test(path))) {
      return { block: true, blockReason: "Protected constitution, guard code/config, or approval state. Propose this change to the owner; do not edit it through agent file tools." };
    }
  }
  return null;
}

export default {
  id: "constitution-guard",
  name: "Constitution Guard",
  description:
    "Blocks Archibald from writing/deleting/unlocking SOUL.md, IDENTITY.md, SECURITY.md, tampering with the Hard Stop mechanism's own state files, or changing OpenClaw's own settings (openclaw CLI config/plugins/approvals/channels/pairing commands, openclaw.json, exec-approvals.json) — propose changes to the user instead. Backed by chflags uchg on the three constitution files.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "constitution-guard", BUILD_STAMP);

    api.on(
      "before_tool_call",
      async (event) => {
        if (event?.toolName === "process") return inspectProcessInput(event?.params);
        if (event?.toolName !== "exec") return inspectStructuredWrite(event?.toolName, event?.params);
        const command = event?.params?.command;
        if (typeof command !== "string") return;

        let decision;
        try {
          decision = inspectConstitutionWrite(command);
        } catch (err) {
          log.warn?.(`[constitution-guard] inspect error: ${err?.message ?? err}`);
          return;
        }
        if (!decision) return;

        log.info?.(
          `[constitution-guard] blocked self-edit attempt\n  cmd: ${command}\n  why: ${decision.blockReason}`,
        );
        return { block: true, blockReason: decision.blockReason };
      },
      { priority: 60 },
    );

    log.info?.("[constitution-guard] before_tool_call handler registered");
  },
};
