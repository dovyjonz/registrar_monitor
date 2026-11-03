---
name: registrar-monitor-telegram
description: Implement or review Registrar Monitor Telegram channel reports, private subscriptions, deep links, commands, notification matching, retries, and bot service operations.
---

# Registrar Monitor Telegram

Read the notification delivery section of `docs/architecture.md` and the Telegram
section of `docs/operations.md`.

## Product contract

- Channel reporting remains the authority for the reportable snapshot pair.
- Stage a personal batch before channel send; activate it only after channel
  success and the reporting-log write.
- `--no-telegram` and debug runs create no personal batch.
- Use only the latest stored snapshot for search/status. Never poll the registrar
  from the bot.
- The bot runs in webhook mode behind a Cloudflare Tunnel, served by its own small
  ASGI app (Starlette under uvicorn, `runtime.create_web_app`). Reject any request
  without the `TELEGRAM_WEBHOOK_SECRET` header and fail startup without the secret
  and an `https` `TELEGRAM_WEBHOOK_URL`. Never open an inbound VM port or bind the
  listener publicly.
- The Mini App API (`subscriptions/miniapp.py`, `/miniapp/*`) lives in the bot, never
  in a Worker. Derive the user only from verified `initData` (HMAC with the bot token,
  24-hour `auth_date` limit); never accept a client-supplied user id. Writes reuse
  the store and catalog checks the chat commands use. CORS allows only the
  `website.base_url` origin. It is off when `website.miniapp_api_url` is empty.
- Browsing, search, and section picking belong to the Mini App. Keep the bot to exact
  codes, deep-link confirmation, listing and removing watches, settings, `/import`,
  and digests; do not rebuild multi-step flows in `interactions.py`.
- Channel report course headings carry one-tap deep links
  (`/start watch_<semester>_<course>`); keep the payload compact, resolve it
  against the latest snapshot, and keep the confirmation step.
- Keep interactions private, acknowledge callbacks first, validate every payload,
  and require confirmation for deep links and destructive actions.
- Course watches supersede overlapping section watches in one digest.
- When `TELEGRAM_BOT_TEST_USER_ID` is set, show `/test` only to that operator and
  do not expose the configured ID. Never use it to restrict ordinary interactions
  or personal delivery.
- Treat IDs and tokens as sensitive. Do not log them.
- Honor `RetryAfter`, back off transient failures, deactivate on `Forbidden`, and
  stay under configured per-chat and global rates.

## Verification

Prefer real temporary SQLite plus fake Telegram boundaries. Cover the
report-to-batch and batch-to-digest seams; avoid exhaustive handler permutations.
Do not start, stop, install, or enable the production bot without explicit
authorization, including the tunnel unit.
