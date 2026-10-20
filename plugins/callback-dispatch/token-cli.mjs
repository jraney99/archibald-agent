// Test helper: run raw inbound texts through the plugin's REAL parser + table.
// stdin: JSON array of strings. stdout: JSON array of {token, handled}.
//   token   — what the parser claims (null = falls through to the agent as plain text)
//   handled — a dispatch-table entry exists, i.e. a script actually runs
// Used by button_contract_test.py so the contract is checked against production
// code, never a reimplementation of it.
import { callbackToken, resolveEntry } from "./index.mjs";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const out = JSON.parse(input).map((raw) => {
  const token = callbackToken(raw);
  return { token, handled: Boolean(token && resolveEntry(token)) };
});
process.stdout.write(JSON.stringify(out));
