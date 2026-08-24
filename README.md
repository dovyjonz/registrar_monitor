# Registrar Monitor

Registrar Monitor downloads registrar spreadsheets, stores enrollment history in
SQLite, reports changes to Telegram, and publishes a static Cloudflare Pages
dashboard. Telegram users can also watch individual courses or sections in a
private chat.

## Setup

Requirements:

- Python and Node.js versions from `.python-version` and `.node-version`
- [`uv`](https://docs.astral.sh/uv/)
- [`jj`](https://jj-vcs.github.io/jj/) for normal repository work

```bash
jj git clone <repo-url>
cd registrar_monitor
make bootstrap
make doctor
```

For an existing Git checkout, run `jj git init --colocate .` once.

Copy `.env.example` to `.env` and set only the credentials you need.
`TELEGRAM_BOT_TOKEN` is shared by channel reporting and the private subscription
bot; `TELEGRAM_CHAT_ID` is used only for the channel. Keep secrets out of
`settings.toml`.

Use `settings.toml` for semesters, registrar URLs, milestones, timezone, paths,
notification limits, storage modes, and website settings.

## Commands

```bash
monitor poll                         # download and store the latest spreadsheet
monitor status "CSCI 151"            # inspect current enrollment
monitor report --stateful            # report new changes
monitor run                          # poll, report, and generate the dashboard
monitor schedule                     # run the monitoring daemon
monitor bot                          # run private Telegram subscriptions
monitor health-monitor               # alert the test operator about service outages
monitor deploy                       # generate the dashboard
monitor deploy --deploy              # generate and upload to Cloudflare Pages
monitor db stats
monitor doctor
```

Add `--no-telegram` to `report`, `run`, or `schedule` for local operation. Put
the global `--debug` option before the command. `monitor --help` is the complete
CLI reference.

Private Telegram users have `/watch`, `/subscriptions`, `/status`, `/settings`,
and `/help`. Set `TELEGRAM_BOT_TEST_USER_ID` to show private `/test` diagnostics
to one operator without restricting ordinary users. See
[`docs/operations/telegram-subscriptions.md`](docs/operations/telegram-subscriptions.md).

## How it fits together

```text
registrar XLS → parse → per-semester SQLite → compare snapshots
                                              ├─ channel report
                                              ├─ personal digests
                                              └─ static dashboard → Pages
```

- Per-semester SQLite databases are the enrollment source of truth.
- Stateful reporting owns the exact snapshot pair used by both channel and
  personal notifications.
- Bot users, subscriptions, and retries live in a separate SQLite database.
- Instructor-only changes remain in history but do not trigger Telegram reports.
- Dashboard files are generated locally and uploaded directly to Cloudflare
  Pages. The preview-image Worker is separate.
- Scheduler and dashboard behavior come from `settings.toml`.

## Repository layout

```text
src/registrarmonitor/
├── automation/       downloads and scheduling
├── cli/              command implementations
├── data/             SQLite, migrations, parsing, and comparisons
├── reporting/        text, Telegram channel, and PDF output
├── services/         workflow orchestration
├── subscriptions/    private Telegram bot and delivery state
└── website/          static read models and page generation

assets/website/       Vite dashboard
scripts/              host setup and maintenance
tests/                Python tests
settings.toml         non-secret configuration
```

Runtime databases, downloads, reports, generated site output, logs, caches, and
coverage are ignored by version control.

## Development

```bash
jj status
jj diff
make check-fast        # Python format, lint, types, and tests
make check             # add frontend lint, tests, and build
```

Use focused tests while iterating. Browser, Worker, security, smoke, and release
gates are opt-in; see
[`docs/operations/tooling.md`](docs/operations/tooling.md).

## Production

Production monitoring runs on `registrarmonitor.service`. The subscription bot
unit is generated as `registrarmonitor-bot.service`. The independent health
monitor is generated as `registrarmonitor-health.service`; it checks both main
units and sends outage/recovery alerts to `TELEGRAM_BOT_TEST_USER_ID`. None of
these generated units are installed or activated by setup unless an operator
performs that rollout explicitly. Repository sync, Pages deployment, and
service-state changes are separate actions.

See
[`docs/operations/production-topology.md`](docs/operations/production-topology.md)
before any VM action. Do not print `.env`, tokens, chat IDs, or unrestricted
journal/process environments.

## Documentation

- [`DATABASE.md`](DATABASE.md) — enrollment and bot storage
- [`CONTEXT.md`](CONTEXT.md) — domain terms
- [`docs/adr/`](docs/adr/) — architectural decisions
- [`docs/operations/telegram-subscriptions.md`](docs/operations/telegram-subscriptions.md)
- [`docs/operations/website-publication.md`](docs/operations/website-publication.md)
- [`docs/operations/tooling.md`](docs/operations/tooling.md)
- [`AGENTS.md`](AGENTS.md) — concise repository rules for coding agents

This is a personal, educational project.
