## Proactive Alerts
Alert immediately: credential failures, API health breaks, suspicious access, cost spikes, time-critical calendar/task issues. Include service name and HTTP status for API failures.

**Routine data feeds are silent (the user, 2026-10-02).** A watcher that ingests bank alerts, statements
or balances (the bank balance feed, the card and payment-app feeds) sends nothing on success:
no daily FYI, no success notice, no balance summary, no email. Balances appear only when the user asks
for a report or in an already-scheduled financial review. Keep ingestion emails delivered and
retained, not deleted or blocked. The user explicitly authorized the bank's routine "Your balance
is below" emails from its exact alerts address to file to `Finance/Banking/<bank>`
and skip the inbox (October 2); archived mail remains readable by the watcher. Keep security,
fraud and actionable bank emails outside that narrow rule. Notify him only when a persistent
problem needs his hand (a daily alert that has stopped, mail that no longer parses, a credential
failure), and deduplicate: one notice after the problem has persisted (the cron failure alert's
"after N consecutive failures" plus a cooldown of days), not one per run. Source order for finance
data: email for what arrives reliably, Plaid for the remaining gaps, statements for reconciliation.
