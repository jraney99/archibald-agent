import assert from "node:assert/strict";
import { statusQuestionArgv, STATUS_ARGV } from "./status-question.mjs";

const ask = (content) => statusQuestionArgv({ content, channel: "telegram" });

// the real question that got a made-up answer on 2026-09-19, and its siblings
for (const q of [
  "How many meals have I hit today?",
  "how many meals so far",
  "How many windows have I logged?",
  "What have I logged today?",
  "What windows did I miss",
  "what did I eat today?",
  "Did I take my vitamins?",
  "did i take my supplements today",
  "Have I had lunch?",
  "How much water have I had?",
  "how many oz of water today?",
  "What's my status today?",
  "status",
  "Today's progress so far 🎩",
]) {
  assert.deepEqual(ask(q), STATUS_ARGV, `should be answered by the script: ${q}`);
}

// another day, a report, a request, or ordinary chat -> left for the agent
for (const q of [
  "How many meals did I hit yesterday?",
  "Did I take my vitamins last night?",
  "how many meals this week",
  "What did I eat on Tuesday?",
  "How many meals did I log on 9/16?",
  "I had a burrito for breakfast",
  "Please draft me an email to Dr. Example",
  "What supplement do I need to take with dinner?",
  "How many meals should I be eating per Dr. Example's plan and why does the snack matter so much to her?",
  "Did I take the trash out?",
  "What's the status of my golf booking?",
  "how many meals today?\nalso move my workout",
  "<media:image> how many meals today",
  "",
]) {
  assert.equal(ask(q), null, `should go to the agent: ${q}`);
}

// the returned argv is a fresh copy — a caller can't mutate the constant
ask("status").push("--oops");
assert.deepEqual(STATUS_ARGV, ["dr-physician/scripts/today_status.py"]);
assert.equal(statusQuestionArgv(undefined), null);
console.log("status-question: all assertions passed");
