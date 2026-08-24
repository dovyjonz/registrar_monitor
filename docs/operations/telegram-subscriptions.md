# Telegram watches

The subscription bot sends private enrollment-change digests for watched courses
and sections. It uses the same bot token as channel reporting and never reads or
changes the configured channel chat ID.

## User commands and navigation

- `/start` opens Add a watch and My watches
- `/watch` accepts an exact course or section, or searches by title
- `/watches` opens watch details and management
- `/help` explains both primary paths

The public Telegram command menu contains only those four commands. The hidden
`/import` transport remains available for starred courses copied from the website.
Browse all courses is a secondary action inside Add a watch. Data and settings is
inside My watches.

The bot works only in private chats. `/watch CSCI 115` immediately adds a whole
course watch. `/watch CSCI 115 / 1L` immediately adds that section. Non-exact
input opens search results. Courses with many sections use paged controls and
also accept a typed, comma- or space-separated list of section IDs. Section
changes use portable labels such as `1L selected`. Review changes shows additions
and removals before Save changes applies the complete scope atomically. Back and
Cancel do not change stored watches. Opening an existing target from `/watches`
shows its latest stored enrollment and observation time instead of removing it.
Stopping a watch, clearing all watches, and deleting bot data require confirmation.

Telegram deep links are ordinary `t.me/<bot>?start=<payload>` links that open the
private bot with a small validated context payload. They let a course page open
the matching bot course without asking the user to search again. They never
subscribe silently: the bot resolves the payload against its latest stored
snapshot and requires confirmation.

Website bookmark import is deliberately independent of any bot username. Each
visitor's bookmarks remain in that browser. The dashboard copies a portable
multi-line `/import` command containing the current semester and starred course
codes; the visitor pastes it into the bot they use. The bot validates every
course against its latest snapshot and presents a whole-course-watch receipt
before applying it. This paste flow is not constrained by Telegram's 64-character
deep-link payload limit. Watches belong to one semester and do not carry forward
automatically. Each digest includes a button back to My watches.

## Runtime

```bash
monitor bot
```

The bot uses `TELEGRAM_BOT_TOKEN` from `.env` and stores state at the
`directories.telegram_bot_state` path in `settings.toml`. It reads current and
historical enrollment directly from the semester databases.

### Developer diagnostics

Set `TELEGRAM_BOT_TEST_USER_ID` in `.env` to one positive numeric Telegram user
ID, then start the bot. Telegram shows `/test` only in that account's command
menu, and the handler rejects the command and its callbacks for everyone else.
Ordinary interactions and personal delivery remain available to every user.
`/test` reports the active semester, latest snapshot,
bot uptime, aggregate user and delivery counts, the configured user's watch
count, and up to five recent identifier-free subscription log records without displaying
the configured ID. Its simulation button
renders one synthetic watched enrollment change through the real personal-digest
formatter without writing enrollment state or creating delivery work.

The setting does not affect channel reporting. Unset it and restart the bot to
remove the diagnostics command.

`scripts/setup_vps.sh` generates `registrarmonitor-bot.service` without installing,
enabling, or starting it. The channel scheduler remains
`registrarmonitor.service`; the two processes must not be collapsed into one unit.
Startup refuses to poll while the bot has an active webhook or another update
consumer. It uses direct Bot API polling because PTB's `Updater` bootstrap deletes
webhooks; this service never calls `deleteWebhook`.

Updates from different private chats run concurrently with a bound of eight.
Updates within one chat remain ordered so section-selection state cannot race.
Catalog reads, delivery snapshot rendering, and bot-state writes run outside the
event loop. Immutable catalogs are reused until the latest snapshot ID changes.
Every update records processing latency without logging chat or user identifiers;
updates taking at least three seconds are warnings.

## Delivery contract

Personal work becomes deliverable only after the matching channel send and
reporting-log write succeed. Retries survive restarts. Telegram flood-control
delays are honored, transient failures back off, and a user who blocks the bot is
deactivated until they message it again.

Completed batches are retained for 90 days. Inactive users without subscriptions
are removed after 180 days; accounts with watches remain until the user clears or
deletes their data. Data and settings in My watches provides immediate deletion.

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
