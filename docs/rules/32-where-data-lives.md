# Where Data Lives

Decided with the user 2026-09-20. Before this there was no rule, so every session picked its own
spot: health logs ended up in three places (the root of `~/.openclaw`, `nutrition/config/`,
`Skills/shared/`), 49 loose files sat at the root, and 101 scripts typed a data path themselves —
which is why nothing was ever tidied (moving one file meant editing many scripts), and why on
2026-09-17 Archibald searched five places before it found the food log.

## The rule

1. **A skill owns its data, in three folders.**
   - `Skills/<skill>/config/` — settings the user chooses. Changes rarely, by decision.
   - `Skills/<skill>/data/` — records that matter: meals, water, medication, movement, contacts.
     What an audit, a backup or the physician's report depends on.
   - `Skills/<skill>/state/` — throwaway bookkeeping: message ids, "last sent" markers, pending
     prompts. Deleting it loses nothing the user would miss.
2. **One owner per file.** Another skill that needs it goes through the owner's script (or the
   helper), never by opening the file on its own path.
3. **`Skills/shared/` holds code only.** No data files.
4. **The root of `~/.openclaw` belongs to OpenClaw** — its config, credentials, runtime files.
   A skill never creates a file there.
5. **No script types a data path.** It asks the helper:
   ```python
   from data_paths import path, new_file
   LOG = path("hydration_log.json")                  # a registered file, wherever it lives today
   NEW = new_file("hydration", "state", "x.json")     # a brand-new file, in the right place
   ```
   and registers a new name in `Skills/shared/data_paths.py`.

`Finance/` and `Health/` are the user's own records, organised by their own maps (`Finance/CLAUDE.md`,
`Health/CLAUDE.md`). This rule is about what skills and scripts keep.

## How it is enforced

`Skills/shared/test_data_layout.py`, run every day by the guard self-check
(`test-fixtures/test-all.mjs` → `guard-suite-daily-selfcheck`). It fails when:

- a new `*.json` file appears at the root of `~/.openclaw`;
- a script names a data file itself instead of asking the helper;
- a data file is added to `Skills/shared/`;
- the registry is unsound, or a name marked moved still has its old file.

What already broke the rule on 2026-09-20 is snapshotted in
`test-fixtures/data-layout-baseline.json`. That list is a ratchet: nothing may be added, and a
fixed entry must be removed (`python3 test_data_layout.py --write-baseline`).

## Moving an existing file (one skill at a time, never in bulk)

Nothing was moved on 2026-09-20. `data_paths.path()` returns a file's **old** location until its
name is added to `MOVED` — always, even if the file does not exist yet — so a script already on
the helper and a script still typing the old path can never disagree about where a file is.

1. Switch every script that touches the file to `path("name")`. Run that skill's tests.
2. In one change: move the file to `Skills/<owner>/<kind>/`, add the name to `MOVED`.
3. Run `test_data_layout.py`, the skill's tests and `node test-fixtures/test-all.mjs`.
4. Also check what is not a script: plugin `.mjs` files, `dispatch-table.json`, cron command
   lines, `memory-health-guard`'s `HEALTH_LOG_FILES` (it matches by file name, so a move is safe
   there, a rename is not).

Do this when a skill is being reworked anyway. The health logs (three places today) are the
first worth doing.
