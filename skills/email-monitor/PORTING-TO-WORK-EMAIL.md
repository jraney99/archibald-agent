# Brief: set up the user's email filing system on his work mailbox

You are setting up, on the user's **work** email, the filing system that already runs on his
personal Gmail. It was built and debugged over several days in September 2026. This brief gives
you the design, the reasons behind each rule, the mistakes already made (so you don't repeat
them), and the decisions that must be made fresh for a work mailbox. Reuse the ideas and the code;
do not copy the personal folders or sender lists.

## What the user wants (his words, condensed)

1. **The inbox is only for conversations with real people.** Everything automated is filed.
2. **File by what an email IS, not who sent it. One email, one folder.** A receipt goes to
   Receipts even if the doctor sent it. He rejected multi-label filing as confusing.
3. **Tell him when something important arrives** (he reads Telegram, not his inbox).
4. **Nothing is ever deleted.** Junk goes to a `Low Priority` folder, never Trash — Gmail empties
   Trash after 30 days, so one wrong rule loses mail for good.
5. **He maintains nothing.** No decisions about where an email goes should ever reach him.
6. He explicitly rejected a to-do/task tracker layered on email. Don't propose one.

## How the system works (three layers, one rulebook)

**One rulebook** (`refile_spec.json`) is the only place filing is decided. First match wins, in
this order:
1. explicit action items → `Action Needed`
2. packages → 3. receipts → 4. documents (to sign / signed)
5. codes, login, password and fraud notices → `Notifications/Security` — from ANY sender
6. then one folder per account or relationship
7. known promotions/surveys → out of the inbox with no folder

**Gmail filters are generated from the rulebook**, never hand-written. Each filter covers one
sender's *ordinary* mail and carries a "doesn't have" clause listing receipt, package, document
and security words, so those stay in the inbox for a minute and get filed by type. Senders that
also send security or money-movement mail are label-only at the filter (they stay visible until
the alert has gone out). Re-running the generator must produce "0 to add, 0 to remove".

**Arrival filing** (runs every minute) applies the same rulebook to inbox mail, asks a fast model
ONE constrained question for senders no rule knows ("what is this?" — receipt, package, document,
code, account_notice, promotion, needs_you, personal, unsure), and lets finished conversations
leave: 14 quiet days if the user wrote last and asked nothing, 30 days if they wrote last. **A thread
where the user asked something and got no reply never leaves on its own** — it becomes a nudge
(follow up / keep waiting / let it go).

Source to reuse, on the user's Mac (also in his private repo `owner/Archibald`):
`~/.openclaw/workspace/Skills/email-monitor/` — `refile_spec.json`, and in `scripts/`:
`refile_rules.py` (engine), `refile_plan.py` + `apply_refile.py` (one-time re-file: dry run,
checksum, undo file), `refile_filter_plan.py` (filter generator), `arrival_filing.py` (arrival
decisions), `cleanup_labels.py`, `networking_plan.py`, and their tests. Read
`Documentation/rules/17-email-gog-owner-gmail-com.md` for the operating description.

## How to work with the user

- **Nothing changes in his mailbox without a literal ✅ from him** on a specific, stated change.
  "ok" or "do it" is not approval. Bundle related changes under a short code (R1, F2…) so he can
  reply "✅ R1 F2". Label-only, reversible filing is where he is most relaxed; sending, deleting,
  filter changes and anything touching accounts always need the ✅.
- **Never send email on his behalf without approval.** Drafts only.
- He asks for "tldr" constantly. Lead with the answer, keep it short, plain words, no jargon.
  Address him as "Master Wayne".
- He wants you to push back. When he proposes something, poke holes in it, then give one
  recommendation rather than a menu.
- Report honestly. When a dry run catches your mistake, say so. When you trashed 28 instead of 26,
  say so.

## The method that worked (follow it in this order)

1. **Audit read-only first.** Count everything: total mail, what is in a folder, what is in the
   inbox split into real people vs automated, every existing filter, Spam and Trash. Show him.
2. **Agree the folder tree before building.** Show the whole tree with counts, as a tree.
3. **Put the rules in one file.** Write tests using real subject lines from his mailbox.
4. **Dry run, then read the ENTIRE list of what each rule matched** before applying. Every dry
   run here caught real errors (see below). A summary count is not enough.
5. **Apply with a checksum-pinned plan, a backup, and an undo file.** Verify afterwards.
6. **Generate the filters from the rulebook.** Confirm each new query matches real mail.
7. **Run arrival filing in watch-only mode first** (decide and log, change nothing), review its
   judgement with him, then switch it on.
8. **Finish with the consistency checks**: rules vs filed mail = 0 changes, filters vs rulebook =
   0/0, no email in two folders, no dead folders, audit passes, total mail count unchanged.

## Mistakes already made — do not repeat them

- **Sender-only matching misfiles mail sent through mailing services.** A brokerage's
  proxy notices came from `proxyvote.com` and were filed as "documents". Match on the display
  name as well as the address (Gmail's `from:"display name"` works in filters).
- **Subject-only rules must never apply to people.** A relative forwarded "Security alert…";
  a friend can write "Re: receipt". Apply type rules to automated mail only.
- **A person-looking sender must never be filed as junk or as a document.** The first preview
  sent a sales rep's emails, and the user's own forwards, to Low Priority. His own addresses are
  never filed.
- **Vacation auto-replies live in Sent and poison "does he correspond with this sender?".** He
  had 80, sent to robots. Ignore any sent message with `Auto-Submitted: auto-replied` /
  `X-Autoreply: yes`. An automated address only counts as a correspondent when the user genuinely
  replied inside that thread (a support ticket).
- **Loose patterns sweep in the wrong mail.** "is on the way" caught "Your W-2 is on the way";
  "please sign" caught "Please Sign In"; "receipt" caught a company called ReceiptMe; bare
  "password" caught a marketing email. Anchor patterns, and keep each one as a regression test.
- **"Needs you" rules must only apply to mail in the inbox**, or months-old "needs your
  attention" notices flood Action Needed. Tools never move mail *out* of Action Needed.
- **Archiving can blind other automations.** Check how anything else finds its mail (by sender?
  by inbox? by folder?) before a filter starts skipping the inbox.
- **Only file a message after any alert about it has been delivered.**
- **When rules change, re-decide what is already sitting in the inbox**, or a fix never reaches it.
- **Never touch Gmail's Spam label**, and never add "never send to spam". Do check Spam for
  genuine mail: real crypto-exchange confirmations were found there.
- **Gmail quirks:** filters can't be edited (delete + create; create first so there is no gap);
  Gmail drops "skip inbox" from a Trash rule's stored action; `subject:receipt` also matches
  "received"; `label:Parent` never matches sub-folders.
- **Testing traps:** a default argument like `def load(path=FILE)` binds at import, so tests wrote
  to the user's real files — resolve paths at call time and sandbox every test. A "mutation test" run
  against a copied folder proved nothing because the tests loaded the real module by absolute
  path — inject the broken behaviour in-process. A mutant that passes means the harness is wrong.
- **Starred means "never move this".** Respect it everywhere.

## What is DIFFERENT for a work mailbox — decide these with the user, don't assume

1. **The folder tree.** Do not copy Finance/Health/Travel. Keep the type-first layer
   (`Receipts & Invoices`, `Documents & Contracts`, `Notifications/Security`, `Calendar`,
   `Shared Files`), then organise the rest by **relationship**: `Customers/<name>`,
   `Investors/<name>`, `Vendors/<name>`, `Team`, `Recruiting`, `Legal`, `Company Finance/<account>`.
   Start with about ten folders. His personal tree grew to ~45 and most of the bugs came from that
   granularity; experts use 3–5 plus search. Add a folder only when he would actually browse it.
2. **Confidentiality.** Arrival filing sends a sender, subject and a few opening lines to a
   language-model API. For work mail, check that this is acceptable under company policy and any
   customer NDAs before enabling it. If not, run rules only and leave unknown senders in the inbox.
3. **Retention and ownership.** Work mail may be subject to retention or legal-hold rules, and
   the mailbox may be administered by someone else. "Never delete" still holds; also don't create
   forwarding, delegation, or anything that moves work mail to a personal account. Keep the two
   mailboxes completely separate: separate credentials, state files, rulebook and logs.
4. **Who never gets auto-archived.** Ask for the work VIP list (the co-founder, investors,
   key customers). Their threads should leave the inbox only when the user archives them or says so.
5. **Nudges matter more at work.** "You asked, no reply in 14 days" is valuable for investor and
   customer follow-ups. Confirm the timings; he may want them shorter.
6. **Outreach folder.** `networking_plan.py` finds threads the user started asking for someone's time.
   At work the equivalent is sales/investor outreach — agree what counts and what it is called.
7. **Alerts.** Decide where work alerts go and at what hours. Do not assume his personal Telegram
   channel is appropriate for confidential work mail.

## What "done" looks like

- Inbox contains only live conversations with people.
- Every automated email has exactly one folder, chosen by the rulebook.
- Filters are generated, and the generator reports nothing to add or remove.
- Arrival filing has run in watch mode, been reviewed with the user, and is live.
- Total mail count is unchanged; every change has an undo file.
- The user can describe the system in two sentences.
