---
status: accepted
date: 2026-08-24
---

# Couple personal notifications to successful channel reports

## Context

The monitor already decides which snapshot pair is reportable and records the
last reported snapshot. Personal alerts must describe that same pair. Running a
second timer or polling enrollment independently could make channel and personal
messages disagree.

Bot users and delivery retries also have a different lifecycle from enrollment
history. Mixing them into the semester databases would complicate retention and
schema ownership.

## Decision

- Keep enrollment and reporting state in the existing per-semester databases.
- Keep users, semester-scoped subscriptions, notification batches, and delivery
  attempts in `data/telegram_bot.db`.
- Stage a batch before the channel send. Record channel success, add the existing
  reporting-log row, then make the batch deliverable.
- Freeze recipient and subscription row IDs when a batch is first staged. Later
  subscriptions, including unsubscribe/re-subscribe cycles, never receive it.
- Recover a pending batch only when it has its own durable channel-success marker
  and the exact current snapshot has a reportable reporting-log row.
- Build personal digests by filtering the existing snapshot comparison and shared
  reportability rules. A course watch supersedes overlapping section watches.
- Run private-chat commands and delivery in a separate long-polling process.

## Consequences

Channel failure cannot produce a personal digest. `--no-telegram` and debug runs
do not create batches. Unsubscribing before dispatch skips queued work. Delivery
is retryable and restart-safe at chunk boundaries.

Telegram cannot provide strict exactly-once visibility across a crash after it
accepts a message but before SQLite records success. The implementation favors
durable progress and may resend that final unrecorded chunk after such a crash.

The bot service and its database can be operated or removed without changing
enrollment history. Production installation and activation remain separate,
explicit operator actions.
