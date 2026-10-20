import assert from "node:assert/strict";
import { callbackToken } from "./index.mjs";
const photo = "<media:image> [file_id:AgACAgEAAxkDAAIw22qua26B]\ncallback_data: outfit:formal-4b7e3309d908";
assert.equal(callbackToken(photo), "outfit:formal-4b7e3309d908");
assert.equal(callbackToken("<media:image> [file_id:x]\ncallback_data: confirm:outfit-4b7e3309d908"), "confirm:outfit-4b7e3309d908");
assert.equal(callbackToken("callback_data: confirm:hydration-32"), "confirm:hydration-32");
assert.equal(callbackToken("skip:whatever-unknown"), "skip:whatever-unknown");      // unknown confirm/skip still claimed
assert.equal(callbackToken("outfit:bogus-zzz"), null);                              // unknown other prefix -> agent
assert.equal(callbackToken("note: buy milk"), null);
assert.equal(callbackToken("This is for a yoga class"), null);
assert.equal(callbackToken("<media:image> [file_id:x]\nhere is my receipt"), null);
assert.equal(callbackToken("confirm:hydration-32\nand also turn off the lights"), null);
console.log("callbackToken: 9 checks passed");
