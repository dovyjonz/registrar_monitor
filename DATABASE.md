# Storage

Registrar Monitor uses SQLite for both enrollment history and Telegram
subscription state. The two concerns use separate databases.

## Enrollment databases

Each semester has a database such as `data/enrollment_fall_2026.db`. The current
schema stores:

- stable course and section identities;
- ordered snapshot metadata and freshness;
- append-only course and section changes;
- materialized latest course and section state;
- bounded full checkpoints for reconstruction;
- the stateful reporting log.

`settings.toml` records each semester's storage mode. Historical semesters are
`finalized`; Fall 2026 uses schema-v2 reads and writes. SQLite remains authoritative
for presence, enrollment, capacity, and overall fill. Raw spreadsheets are input
evidence, not the runtime read path.

The key v2 tables are:

| Table | Purpose |
|---|---|
| `course_catalog`, `section_catalog` | Stable identities |
| `state_snapshot` | Ordered observations and state hashes |
| `course_latest_state`, `section_latest_state` | Current state |
| `course_change_event`, `section_change_event` | Historical changes |
| `state_checkpoint` and checkpoint rows | Bounded reconstruction |
| `reporting_log_v2` | Last reported snapshot and reportability |

See
[`docs/adr/0001-checkpointed-enrollment-state.md`](docs/adr/0001-checkpointed-enrollment-state.md)
for the data model and migration evidence.

## Subscription database

`data/telegram_bot.db` is owned by the private Telegram bot. It contains no
enrollment copy.

| Table | Purpose |
|---|---|
| `bot_user` | Private chat identity, activity, and retention timestamps |
| `subscription` | Semester-scoped course and section watches |
| `notification_batch` | Exact channel-reported snapshot pair and lifecycle |
| `batch_recipient` | Recipients fixed when a batch is staged |
| `batch_subscription` | Exact targets fixed when a batch is staged |
| `delivery` | Retry, lease, chunk progress, and terminal state |

The store uses foreign keys, WAL mode, a busy timeout, idempotent unique keys, and
short `BEGIN IMMEDIATE` transactions. Network calls never run inside a database
transaction.

Completed batches are retained for 90 days. Inactive users without subscriptions
are removed after 180 days; subscribed users remain until they clear or delete
their data.

See
[`docs/adr/0002-telegram-subscription-delivery.md`](docs/adr/0002-telegram-subscription-delivery.md).

## Inspection

```bash
monitor db stats
monitor doctor
monitor doctor --json
```

The doctor runs `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, and schema
version checks without printing secrets. Existing enrollment databases are
inspected on open and are never silently upgraded or downgraded.

## Maintenance

```bash
monitor db cleanup --keep 50
```

Migration, rehearsal, mode transition, finalization, and rollback commands remain
available under `monitor db --help`. They require explicit semester, database,
and report paths; apply/finalization steps also require their authorization flag.
Use disposable copies for rehearsals and benchmarks.

Back up enrollment databases and `.env` separately. Stop the relevant writer or
use SQLite's backup mechanism before copying a live database. Bot state can be
backed up independently from semester history.
