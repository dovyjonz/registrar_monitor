# Telegram subscriptions

The subscription bot sends private enrollment-change digests for watched courses
and sections. It uses the same bot token as channel reporting and never reads or
changes the configured channel chat ID.

## User commands

- `/watch` — search and add a course or section
- `/subscriptions` — list or remove watches
- `/status [COURSE]` — choose a watch or show the latest stored enrollment
- `/settings` — clear subscriptions or delete bot data
- `/help` — show commands

The bot works only in private chats. Opening a deep link shows the target's current
status and a confirmation; it never subscribes silently. Watches belong to one
semester and do not carry forward automatically. Each digest includes a button
back to subscription management.

## Runtime

```bash
monitor bot
```

The bot uses `TELEGRAM_BOT_TOKEN` from `.env` and stores state at the
`directories.telegram_bot_state` path in `settings.toml`. It reads current and
historical enrollment directly from the semester databases.

### Developer test mode

Set `TELEGRAM_BOT_TEST_USER_ID` in `.env` to one positive numeric Telegram user
ID, then start the bot. Only that account can interact with the private bot or
receive personal digests. `/test` reports the active semester, latest snapshot,
and subscription count without displaying the configured ID.

Other users receive no replies. Any of their already-queued personal deliveries
are marked skipped so disabling test mode cannot send a surprise backlog. Channel
reporting is unchanged. Unset the variable and restart the bot to return to normal
operation.

`scripts/setup_vps.sh` generates `registrarmonitor-bot.service` without installing,
enabling, or starting it. The channel scheduler remains
`registrarmonitor.service`; the two processes must not be collapsed into one unit.
Startup refuses to poll while the bot has an active webhook or another update
consumer. It uses direct Bot API polling because PTB's `Updater` bootstrap deletes
webhooks; this service never calls `deleteWebhook`.

## Delivery contract

Personal work becomes deliverable only after the matching channel send and
reporting-log write succeed. Retries survive restarts. Telegram flood-control
delays are honored, transient failures back off, and a user who blocks the bot is
deactivated until they message it again.

Completed batches are retained for 90 days. Inactive users without subscriptions
are removed after 180 days; subscribed accounts remain until the user clears or
deletes their data. `/settings` provides immediate deletion.

## Checks

```bash
uv run pytest --no-cov \
  tests/test_subscription_store.py \
  tests/test_subscription_publication.py \
  tests/test_subscription_dispatcher.py \
  tests/test_subscription_catalog.py
uv run monitor --help
bash -n scripts/setup_vps.sh
```

For service checks, bound journal output and never print `.env`, Telegram IDs, or
process environments.
