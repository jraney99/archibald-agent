import assert from "node:assert/strict";
import plugin, { NO_MONEY_TOOLS, OPTIONS_TOOLS, READ_ONLY_TOOLS, TRADE_TOOLS, inspectToolCall } from "./index.mjs";

let pass = 0;
const t = async (name, fn) => {
  try {
    await fn();
    pass++;
    console.log(`  ok  ${name}`);
  } catch (e) {
    console.error(`FAIL  ${name}\n      ${e.message}`);
    process.exitCode = 1;
  }
};
const chat = { sessionKey: "agent:main:telegram:direct:1000000001" };
const cron = { jobId: "c1" };
const kind = (r) => (r?.block ? "block" : r?.requireApproval ? "approval" : "allow");
const order = { account_number: "ACCT0000", symbol: "AAPL", side: "buy", type: "limit", quantity: "10", limit_price: "182.50" };

// --- brokerage tools ---
await t("the lists cover the brokerage's 81 tools without overlap", () => {
  const all = [...READ_ONLY_TOOLS, ...NO_MONEY_TOOLS, ...TRADE_TOOLS, ...OPTIONS_TOOLS];
  assert.deepEqual([READ_ONLY_TOOLS.size, NO_MONEY_TOOLS.size, TRADE_TOOLS.size, OPTIONS_TOOLS.size], [56, 15, 8, 2]);
  assert.equal(new Set(all).size, 81);
});
await t("reads, previews and watchlist/alert edits pass", () => {
  for (const tool of ["get_portfolio", "get_equity_positions", "review_equity_order", "preview_crypto_order", "add_to_watchlist", "mark_alerts_read"]) {
    assert.equal(kind(inspectToolCall(`brokerage__${tool}`, order, chat)), "allow", tool);
  }
});
await t("every order and cancel is blocked with the proposal command", () => {
  for (const tool of TRADE_TOOLS) {
    const r = inspectToolCall(`brokerage__${tool}`, order, chat);
    assert.equal(kind(r), "block", tool);
    assert.match(r.blockReason, new RegExp(`propose_trade\\.py --tool ${tool} --params`));
  }
});
await t("never asks through OpenClaw's own approval card", () => {
  for (const tool of [...TRADE_TOOLS, ...OPTIONS_TOOLS, "something_new"]) {
    assert.equal(inspectToolCall(`brokerage__${tool}`, order, chat)?.requireApproval, undefined, tool);
  }
});
await t("tool-name case doesn't matter", () => assert.equal(kind(inspectToolCall("Brokerage__Place_Equity_Order", order, chat)), "block"));
await t("options orders and exercises are blocked", () => {
  for (const tool of OPTIONS_TOOLS) assert.match(inspectToolCall(`brokerage__${tool}`, {}, chat).blockReason, /switched off/);
});
await t("a brokerage tool nobody listed is blocked", () =>
  assert.match(inspectToolCall("brokerage__replace_option_order", {}, chat).blockReason, /isn't enabled/));
await t("any brokerage tool from a cron run -> block, even read-only", () => {
  assert.equal(kind(inspectToolCall("brokerage__place_equity_order", order, cron)), "block");
  assert.equal(kind(inspectToolCall("brokerage__get_portfolio", {}, cron)), "block");
});
const market = new Set(["job-market-check"]);
const marketRun = { jobId: "job-market-check" };
await t("the approved market-check job may look and propose, never place", () => {
  assert.equal(kind(inspectToolCall("brokerage__get_portfolio", {}, marketRun, market)), "allow");
  assert.equal(kind(inspectToolCall("brokerage__review_equity_order", order, marketRun, market)), "allow");
  assert.equal(kind(inspectToolCall("brokerage__place_equity_order", order, marketRun, market)), "block");
  assert.equal(kind(inspectToolCall("brokerage__place_option_order", order, marketRun, market)), "block");
  assert.equal(kind(inspectToolCall("exec", { command: "curl https://api.broker.example/orders/" }, marketRun, market)), "block");
});
await t("any other job is still blocked", () =>
  assert.equal(kind(inspectToolCall("brokerage__get_portfolio", {}, { jobId: "job-email" }, market)), "block"));
await t("the job list file loads, and a missing file means no jobs", async () => {
  const { loadTradingJobs } = await import("./index.mjs");
  assert.ok(loadTradingJobs() instanceof Set);
  assert.equal(loadTradingJobs(new URL("./no-such-file.json", import.meta.url)).size, 0);
});

// --- Proposals ---
const propose = `python3 ~/.openclaw/workspace/Skills/brokerage-trading/scripts/propose_trade.py --tool place_equity_order --params '${JSON.stringify(order)}'`;
await t("a proposal from chat passes", () => assert.equal(kind(inspectToolCall("exec", { command: propose }, chat)), "allow"));
await t("a proposal from a cron run is blocked", () => assert.equal(kind(inspectToolCall("exec", { command: propose }, cron)), "block"));
await t("a proposal from the market-check job passes", () =>
  assert.equal(kind(inspectToolCall("exec", { command: propose }, { jobId: "job-market-check" }, new Set(["job-market-check"]))), "allow"));

// --- No way around the tap ---
for (const [tool, params] of [
  ["exec", { command: "curl -s -H 'Authorization: Bearer x' https://agent.broker.example/mcp/trading -d @order.json" }],
  ["exec", { command: "python3 /tmp/broker.py  # posts to api.broker.example/orders/" }],
  ["exec", { command: "cat ~/.openclaw/mcp-oauth/brokerage-0123456789abcdef.json" }],
  ["read", { path: "~/.openclaw/mcp-oauth/brokerage-0123456789abcdef.json" }],
  ["write", { path: "/tmp/broker.py", content: "import requests\nrequests.post('https://API.Broker.example/orders/')" }],
  ["web_fetch", { url: "https://agent.broker.example/mcp/trading" }],
  ["exec", { command: "python3 ~/.openclaw/workspace/Skills/brokerage-trading/scripts/trade_callback.py --action approve --approval-id 0123" }],
  ["exec", { command: "cd ~/.openclaw/workspace/Skills/brokerage-trading/scripts && python3 -c 'import broker_mcp; broker_mcp.call_tool(\"place_equity_order\", {})'" }],
  ["write", { path: "/tmp/x.py", content: "from trade_callback import answer" }],
  ["exec", { command: "sqlite3 ~/.openclaw/state/approvals.sqlite 'select id from approvals'" }],
  ["exec", { command: "python3 ~/.openclaw/workspace/Skills/brokerage-trading/scripts/daytrade.py tick" }],
  ["write", { path: "~/.openclaw/workspace/Skills/brokerage-trading/state/day_trades.json", content: "{}" }],
  ["exec", { command: "cd ~/.openclaw/workspace/Skills/brokerage-trading/scripts && python3 backtest.py dip" }],
  ["write", { path: "/tmp/y.py", content: "import dipbuy" }],
]) {
  await t(`block ${tool}: ${JSON.stringify(params).slice(0, 60)}`, () => assert.equal(kind(inspectToolCall(tool, params, chat)), "block"));
}
for (const [tool, params] of [
  ["exec", { command: "gog gmail search 'from:noreply@broker.example newer_than:7d'" }],
  ["web_fetch", { url: "https://broker.example/support/agentic-trading-overview/" }],
  ["exec", { command: "ls ~/.openclaw" }],
  ["message", { text: "Your brokerage statement is ready." }],
  ["read", { path: "~/.openclaw/workspace/Skills/brokerage-trading/SKILL.md" }],
  ["read", { path: "~/.openclaw/workspace/Skills/brokerage-trading/config/daytrade.json" }],
]) {
  await t(`allow ${tool}: ${JSON.stringify(params).slice(0, 60)}`, () => assert.equal(kind(inspectToolCall(tool, params, chat)), "allow"));
}

// --- Registered hook ---
const hooks = new Map();
plugin.register({ on: (name, fn, opts) => hooks.set(name, { fn, opts }), logger: { info() {}, warn() {} } });
const hook = hooks.get("before_tool_call");
await t("registers before_tool_call", () => assert.ok(hook));
await t("hook: order blocked, read passes, bypass blocked, cron blocked", async () => {
  assert.equal(kind(await hook.fn({ toolName: "brokerage__place_equity_order", params: order }, chat)), "block");
  assert.equal(kind(await hook.fn({ toolName: "brokerage__get_portfolio", params: {} }, chat)), "allow");
  assert.equal(kind(await hook.fn({ toolName: "exec", params: { command: "curl https://agent.broker.example" } }, chat)), "block");
  assert.equal(kind(await hook.fn({ toolName: "brokerage__get_portfolio", params: {} }, cron)), "block");
  assert.equal(kind(await hook.fn({ toolName: "exec", params: { command: "gog gmail search in:inbox" } }, chat)), "allow");
});
await t("hook fails closed on a brokerage tool", async () => {
  const poison = new Proxy({}, { get() { throw new Error("boom"); } });
  const r = await hook.fn({ toolName: "brokerage__get_portfolio", params: {} }, poison);
  assert.equal(r?.block, true);
});
await t("hook with no context: order still blocked", async () =>
  assert.equal(kind(await hook.fn({ toolName: "brokerage__place_equity_order", params: order })), "block"));

console.log(`\n${pass} passed`);
import { runRuntimeFixtures } from "../../test-fixtures/runtime-fixtures.mjs";
await runRuntimeFixtures("trading-guard");
