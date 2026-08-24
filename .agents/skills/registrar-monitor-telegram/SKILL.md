---
name: registrar-monitor-telegram
description: Implement or review Registrar Monitor Telegram channel reports, private subscriptions, deep links, commands, notification matching, retries, and bot service operations.
---

# Registrar Monitor Telegram

Read `CONTEXT.md`, `docs/adr/0002-telegram-subscription-delivery.md`, and
`docs/operations/telegram-subscriptions.md`.

## Product contract

- Channel reporting remains the authority for the reportable snapshot pair.
- Stage a personal batch before channel send; activate it only after channel
  success and the reporting-log write.
- `--no-telegram` and debug runs create no personal batch.
- Use only the latest stored snapshot for search/status. Never poll the registrar
  from the bot.
- Keep interactions private, acknowledge callbacks first, validate every payload,
  and require confirmation for deep links and destructive actions.
- Course watches supersede overlapping section watches in one digest.
- When `TELEGRAM_BOT_TEST_USER_ID` is set, enforce it at both the interaction and
  delivery boundaries. Ignore other users and terminally skip their queued work;
  do not expose the configured ID.
- Treat IDs and tokens as sensitive. Do not log them.
- Honor `RetryAfter`, back off transient failures, deactivate on `Forbidden`, and
  stay under configured per-chat and global rates.

## Verification

Prefer real temporary SQLite plus fake Telegram boundaries. Cover the
report-to-batch and batch-to-digest seams; avoid exhaustive handler permutations.
Do not start, stop, install, or enable the production bot without explicit
authorization.
