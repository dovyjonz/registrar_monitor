# Domain glossary

- **Enrollment snapshot** — the complete stored registrar state for one semester
  at one observed time.
- **Report cycle** — comparison of the latest snapshot with the last reported
  snapshot, followed by a channel report when enrollment changes are reportable.
- **Reportable change** — course/section presence, enrollment, or capacity change.
  Instructor-only changes remain in history but do not trigger Telegram reports.
- **Channel report** — the existing full Telegram report sent to the configured
  channel chat.
- **Subscription target** — one course or section in one semester watched by one
  Telegram user.
- **Notification batch** — the durable personal-notification work for the exact
  snapshot pair used by a successful channel report.
- **Deliverable batch** — a batch whose channel send and reporting-log entry both
  succeeded.
- **Personal digest** — one user's reportable subset of a deliverable batch.
- **Developer diagnostics access** — optional private `/test` command shown only
  to one configured Telegram operator. It does not restrict ordinary bot users
  or personal delivery.
- **Enrollment store** — the per-semester SQLite database; the enrollment source
  of truth.
- **Bot store** — the separate SQLite database for users, subscriptions, batches,
  and delivery state. It never copies enrollment state.
- **Generated dashboard** — static HTML and immutable data assets uploaded directly
  to Cloudflare Pages.
