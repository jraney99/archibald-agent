import assert from "node:assert/strict";
import { mealReplyArgv } from "./reply-nudge.mjs";

const index = { "4812": { date: "2026-09-19", slot: 3 }, "4700": { date: "2026-09-18", slot: 1 } };
const ev = (content, extra = {}) => ({ content, channel: "telegram", ...extra });

// a reply to an indexed nudge -> confirm_meal.py with his words untouched
assert.deepEqual(mealReplyArgv(ev("Chipotle bowl, extra chicken", { replyToId: "4812" }), index),
  ["nutrition/scripts/confirm_meal.py", "--date", "2026-09-19", "--slot", "3", "--action", "eaten",
   "--what", "Chipotle bowl, extra chicken"]);
// numeric id, and a prefixed full id
assert.equal(mealReplyArgv(ev("burrito", { replyToId: 4700 }), index)[4], "1");
assert.equal(mealReplyArgv(ev("burrito", { replyToIdFull: "1000000001:4700" }), index)[2], "2026-09-18");
// not a reply / reply to something else / a question / empty -> left for the agent
assert.equal(mealReplyArgv(ev("had a burrito"), index), null);
assert.equal(mealReplyArgv(ev("burrito", { replyToId: "9999" }), index), null);
assert.equal(mealReplyArgv(ev("what's in the BBQ bowl?", { replyToId: "4812" }), index), null);
assert.equal(mealReplyArgv(ev("   ", { replyToId: "4812" }), index), null);
// a corrupt index entry never produces a command
assert.equal(mealReplyArgv(ev("x", { replyToId: "1" }), { "1": { date: "today", slot: "3" } }), null);
// a button tap is never a reply-with-words case in index.mjs (TOKEN_RE wins first); sanity: shell metachars stay one argv item
assert.equal(mealReplyArgv(ev('pasta"; rm -rf ~', { replyToId: "4812" }), index).at(-1), 'pasta"; rm -rf ~');
console.log("reply-nudge: all assertions passed");
