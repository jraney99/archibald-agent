import { pluginBuildStamp, logPluginBuildStamp } from "../.lib/build-stamp.mjs";
export const BUILD_STAMP = pluginBuildStamp(import.meta.url);

/*
 * trading-guard — Archibald can only PROPOSE a brokerage trade; the user's tap places it.
 *
 * WHY THIS EXISTS
 * Built 2026-09-21 when Archibald was connected to the brokerage's agentic-trading service
 * (official MCP server https://agent.broker.example/mcp/trading, server name
 * "brokerage" in openclaw.json). The brokerage's own "review before trading" is
 * only the agent choosing to ask. Archibald reads untrusted email all day and
 * runs shell commands unasked, so a planted instruction could otherwise turn
 * into a real order. The user chose: look freely, trade only with his tap, and the
 * tap message must read as Archibald's proposal in plain words.
 *
 * HOW A TRADE HAPPENS
 * Archibald runs brokerage-trading/scripts/propose_trade.py -> the user gets
 * "🤖 Archibald proposes this brokerage trade: …" with Approve / Deny -> his tap reaches
 * the callback-dispatch plugin (never the agent) -> trade_callback.py places
 * exactly that order. See workspace/Skills/brokerage-trading/SKILL.md.
 *
 * WHAT IT DOES (before_tool_call, every tool)
 *   - Brokerage tools (brokerage__*) that read, preview an order, or edit
 *     watchlists/alerts/screeners -> allowed.
 *   - Brokerage order/cancel tools -> BLOCK, with the propose_trade.py command.
 *   - Options orders/exercises and any brokerage tool not listed -> BLOCK.
 *   - Any brokerage tool, or propose_trade.py, from a cron/job run -> BLOCK,
 *     except agent jobs the user approves in trading_jobs.json (none today — day
 *     trading runs as plain scripts, not agent turns). Listed jobs can look and
 *     propose; they still can't place anything.
 *   - Any other tool naming the brokerage's trading hosts (agent./api.broker.example),
 *     OpenClaw's MCP login-token folder (mcp-oauth), the order-placing scripts
 *     (trade_callback, broker_mcp, daytrade.py — its exit watcher sells), the day-trade
 *     records or the approval database -> BLOCK.
 *   - If this guard itself fails on a brokerage tool -> BLOCK (fail closed).
 *
 * WHAT IT LEAVES ALONE
 * Brokerage emails (noreply@broker.example), public broker.example pages, and
 * everything else. Archibald's 3,921 past sessions never named any blocked
 * host, folder, script or database.
 *
 * LIMITS
 * Pattern matching, not a sandbox: Archibald runs as the same Mac user. A name
 * assembled from pieces, a glob or an encoding can get past the name blocks.
 */

import { readFileSync } from "node:fs";

const SERVER_PREFIX = "brokerage__";
// Cron jobs allowed to look at the brokerage and propose trades. Guard data: the
// constitution-guard blocks Archibald from editing anything under this folder.
const JOBS_FILE = new URL("./trading_jobs.json", import.meta.url);
export function loadTradingJobs(file = JOBS_FILE) {
  try {
    return new Set((JSON.parse(readFileSync(file, "utf8")).jobs ?? []).map(String));
  } catch {
    return new Set();
  }
}
const TRADING_JOBS = loadTradingJobs();

// From the brokerage's own tools/list, 2026-09-21 (server brokerage-trading 1.5.0,
// 81 tools). A tool the brokerage adds later is on none of these lists: blocked.
//
// Marked readOnlyHint by the brokerage (52), plus the 4 order previews, which
// simulate an order and place nothing.
export const READ_ONLY_TOOLS = new Set([
  "get_accounts", "get_advanced_orders", "get_alert_log", "get_alerts", "get_crypto_account_onboarding_info",
  "get_crypto_orders", "get_crypto_positions", "get_crypto_quotes", "get_currency_pairs", "get_earnings_calendar",
  "get_earnings_results", "get_equity_analyst_ratings", "get_equity_fundamentals", "get_equity_historicals",
  "get_equity_news", "get_equity_orders", "get_equity_positions", "get_equity_price_book", "get_equity_quotes",
  "get_equity_tax_lots", "get_equity_technical_indicators", "get_equity_tradability", "get_financials",
  "get_index_historicals", "get_index_quotes", "get_indexes", "get_limited_margin_upgrade_info", "get_option_chains",
  "get_option_historicals", "get_option_instruments", "get_option_level_upgrade_info", "get_option_orders",
  "get_option_positions", "get_option_quotes", "get_option_watchlist", "get_pnl_trade_history", "get_politician_trades",
  "get_popular_watchlists", "get_portfolio", "get_realized_pnl", "get_scanner_datapoints", "get_scanner_filter_specs",
  "get_scans", "get_sec_filing", "get_sec_filing_facts", "get_sec_filing_facts_catalog", "get_sec_filing_index",
  "get_watchlist_items", "get_watchlists", "preview_scan", "run_scan", "search",
  "preview_crypto_order", "review_advanced_order", "review_equity_order", "review_option_order",
]);
// Watchlists, alerts and saved screeners (15): they change the user's brokerage
// settings but can't move money, so no tap.
export const NO_MONEY_TOOLS = new Set([
  "add_option_to_watchlist", "add_to_watchlist", "create_alert", "create_scan", "create_watchlist", "delete_alert",
  "follow_watchlist", "mark_alerts_read", "remove_from_watchlist", "remove_option_from_watchlist",
  "unfollow_watchlist", "update_alert", "update_scan_config", "update_scan_filters", "update_watchlist",
]);
// Orders and cancels (8): only through a proposal the user approves.
export const TRADE_TOOLS = new Set([
  "place_equity_order", "place_crypto_order", "place_advanced_order", "cancel_equity_order",
  "cancel_crypto_order", "cancel_option_order", "cancel_advanced_order", "cancel_option_exercise",
]);
// Options orders/exercises (2) name the contract only by instrument UUID, so a
// proposal couldn't show the user the strike or expiry. Off until he asks.
export const OPTIONS_TOOLS = new Set(["place_option_order", "exercise_option"]);

const PROPOSE = "~/.openclaw/workspace/Skills/brokerage-trading/scripts/propose_trade.py";
const BYPASS_RE =
  /\b(?:agent|api)\.broker\.example\b|\bmcp-oauth\b|\btrade_callback\b|\bbroker_mcp\b|\bapprovals\.sqlite\b|\bdaytrade\.py\b|\bday_trades\.json\b|\bbacktest\.py\b|\bdipbuy\b/i;
const PROPOSE_RE = /\bpropose_trade\b/;

const tradeReason = (tool) =>
  "You don't place or cancel brokerage orders yourself. Send it to Master Wayne as a proposal — he gets it in plain " +
  "words with Approve / Deny and his tap places it: " +
  `python3 ${PROPOSE} --tool ${tool} --params '<the same arguments as JSON>'. Don't also ask him in chat.`;
const CRON_REASON =
  "Brokerage actions and trade proposals never come from this scheduled job. Tell Master Wayne in conversation instead.";
const OPTIONS_REASON =
  "Options orders and exercises are switched off: a proposal can't show which contract it is. " +
  "Tell Master Wayne what you'd do; he can turn options on himself.";
const UNKNOWN_REASON =
  "This brokerage tool isn't enabled for you. Tell Master Wayne what you wanted to do.";
const BYPASS_REASON =
  "Brokerage orders are placed only by Master Wayne's tap on a proposal. Don't contact the brokerage's trading service, " +
  "its login token, the order-placing scripts or the approval records any other way.";
const FAIL_CLOSED_REASON = "The trading guard could not check this brokerage action, so it was stopped.";

/** Every string inside a tool call's arguments. */
function stringsIn(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) for (const item of value) stringsIn(item, out);
  else if (value && typeof value === "object") for (const item of Object.values(value)) stringsIn(item, out);
  return out;
}

const block = (blockReason) => ({ block: true, blockReason });

/**
 * @returns {null | {block: true, blockReason: string}}
 */
export function inspectToolCall(toolName, params = {}, ctx = {}, tradingJobs = TRADING_JOBS) {
  const name = String(toolName ?? "").toLowerCase();
  const strangerJob = Boolean(ctx?.jobId) && !tradingJobs.has(String(ctx.jobId));
  if (name.startsWith(SERVER_PREFIX)) {
    if (strangerJob) return block(CRON_REASON);
    const tool = name.slice(SERVER_PREFIX.length);
    if (READ_ONLY_TOOLS.has(tool) || NO_MONEY_TOOLS.has(tool)) return null;
    if (TRADE_TOOLS.has(tool)) return block(tradeReason(tool));
    if (OPTIONS_TOOLS.has(tool)) return block(OPTIONS_REASON);
    return block(UNKNOWN_REASON);
  }
  const texts = stringsIn(params);
  if (texts.some((s) => BYPASS_RE.test(s))) return block(BYPASS_REASON);
  if (strangerJob && texts.some((s) => PROPOSE_RE.test(s))) return block(CRON_REASON);
  return null;
}

export default {
  id: "trading-guard",
  name: "Trading Guard",
  description:
    "Archibald can only propose brokerage trades (propose_trade.py); the user's Approve tap places them. Blocks direct " +
    "order tools, cron trading, options, and every other route to the brokerage's trading service or its login token.",
  configSchema: { type: "object", additionalProperties: false },

  register(api) {
    const log = api.logger ?? console;
    logPluginBuildStamp(api, "trading-guard", BUILD_STAMP);
    api.on(
      "before_tool_call",
      async (event, ctx) => {
        const toolName = event?.toolName;
        let decision;
        try {
          decision = inspectToolCall(toolName, event?.params ?? {}, ctx ?? {});
        } catch (err) {
          log.warn?.(`[trading-guard] inspect error: ${err?.message ?? err}`);
          if (String(toolName ?? "").toLowerCase().startsWith(SERVER_PREFIX)) return block(FAIL_CLOSED_REASON);
          return;
        }
        if (!decision) return;
        log.info?.(`[trading-guard] blocked ${toolName}${ctx?.jobId ? ` (job ${ctx.jobId})` : ""}: ${decision.blockReason.slice(0, 120)}`);
        return decision;
      },
      { priority: 60 },
    );
    log.info?.(`[trading-guard] before_tool_call handler registered (${TRADING_JOBS.size} trading job(s))`);
  },
};
