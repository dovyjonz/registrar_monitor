# Architecture

Registrar Monitor downloads registrar spreadsheets, stores enrollment history in
SQLite, reports changes to Telegram, and publishes a static dashboard as
Cloudflare Workers Static Assets. Telegram users can also watch courses or sections
in a private chat or in the dashboard opened as a Telegram Mini App.

```text
registrar XLS → automation/ → data/ (per-semester SQLite)
                                ├─ services/reporting → reporting/ → Telegram channel
                                │                     └─ subscriptions/ (bot DB, digests)
                                └─ services/website → website/ → static site → wrangler deploy
Telegram → Cloudflare Tunnel → bot (subscriptions/): webhook + Mini App API
workers/site            Worker: Static Assets (the site) + /preview/* images
frontend/               Vite + Chart.js dashboard source
```

## Glossary

- **Enrollment snapshot**: the complete stored registrar state for one semester at
  one observed time.
- **Report cycle**: comparison of the latest snapshot with the last reported
  snapshot, followed by a channel report when the changes are reportable.
- **Reportable change**: course or section presence, enrollment, or capacity
  change. Instructor-only changes stay in history but do not trigger reports.
- **Channel report**: the full Telegram report sent to the configured channel chat.
- **Subscription target**: one course or section in one semester watched by one
  Telegram user.
- **Notification batch**: the durable personal-notification work for the exact
  snapshot pair used by a successful channel report.
- **Deliverable batch**: a batch whose channel send and reporting-log entry both
  succeeded.
- **Personal digest**: one user's reportable subset of a deliverable batch.
- **Developer diagnostics access**: optional private `/test` command shown only to
  the operator in `TELEGRAM_BOT_TEST_USER_ID`. It never restricts ordinary users.
- **Enrollment store**: the per-semester SQLite database; the enrollment source of
  truth.
- **Bot store**: the separate SQLite database for users, subscriptions, batches,
  and delivery state. It never copies enrollment state.
- **Watch deep link**: `t.me/<bot>?start=watch_<semester>_<course>`, placed on each
  course heading in a channel report; the bot confirms before it adds the watch.
- **Mini App**: the generated dashboard opened inside Telegram; it adds Watch and
  Unwatch buttons that call the bot's `/miniapp/*` API with signed `initData`.
- **Generated dashboard**: static HTML and immutable data files in `frontend/public`,
  deployed as the Static Assets of the `workers/site` Worker.

## Enrollment storage

Each semester has one database such as `data/enrollment_fall_2026.db`. All of them
use schema v2 (`PRAGMA user_version = 2`); a database with any other version is
rejected on open. Schema changes are forward-only migrations in code. New
semesters start with `monitor db initialize --semester "<name>"`. SQLite is
authoritative for presence, enrollment, capacity, and overall fill; raw
spreadsheets are input, not a read path.

| Table | Purpose |
|---|---|
| `course_catalog`, `section_catalog` | Stable identities (`course_code`; `(course, section_code)`) |
| `state_snapshot` | Ordered observations: `sequence_no`, `observed_at`, `last_seen_at`, `overall_fill`, `state_hash` |
| `course_latest_state`, `section_latest_state` | Present entities only; removal deletes the row |
| `course_change_event`, `section_change_event` | Append-only `ADD`/`UPDATE`/`REMOVE` with old and new values |
| `state_checkpoint`, `course_checkpoint_state`, `section_checkpoint_state` | Complete state for bounded reconstruction |
| `reporting_log_v2` | Last reported snapshot and whether it had changes |

Exact columns and constraints are in `src/registrarmonitor/data/checkpointed_state.py`.

### Write model

A distinct canonical state writes, in one transaction: a snapshot row, one event
per changed course or section, latest-state updates (only changed rows), and a
checkpoint when due. An identical consecutive observation only advances the newest
snapshot's `last_seen_at`. Returning to an older state after a different one creates
a new snapshot even though `state_hash` repeats; the hash is not unique.

A checkpoint holds every present course and section. It is created for the first
state and for the first state reaching 96 distinct states or 2,048 course and
section events since the previous checkpoint.

### Invariants

1. `sequence_no` (ascending `observed_at`, contiguous per semester) is the only
   ordering key. `snapshot_id` is an identity, never an order.
2. Every snapshot reconstructs from the nearest preceding checkpoint plus events in
   `sequence_no` order, and latest-state tables equal reconstruction of the newest
   snapshot.
3. Additions and removals are explicit events. An `ADD` has only new values, a
   `REMOVE` only old values, an `UPDATE` both with at least one change.
4. Instructor removal is `old -> ""` and reappearance is `"" -> new`; absence and a
   blank instructor are different.
5. Deduplication never changes `observed_at`, event times, or history hashes.
6. `last_seen_at` is never earlier than `observed_at`.

### Persisted and derived values

Persisted: identities, presence, enrollment, capacity, section type, instructor,
title, department, timestamps, `overall_fill` (computed from all filtered registrar
rows before section deduplication, so not reconstructible), event old/new values,
checkpoints, and the SHA-256 `state_hash` (semester, `overall_fill`, sorted courses
and sections with their values; never IDs or timestamps).

Derived: section fill (`enrollment / capacity`); `OPEN` below 0.75, `NEAR` from 0.75
up to 1.0, `FULL` at 1.0 or more; course average fill; a course is filled when every
section of at least one section type is full; course totals are the minimum of the
per-section-type sums.

## Subscription storage

`data/telegram_bot.db` belongs to the private bot and contains no enrollment copy.
It uses foreign keys, WAL, a busy timeout, idempotent unique keys, and short
`BEGIN IMMEDIATE` transactions; network calls never run inside a transaction.

| Table | Purpose |
|---|---|
| `bot_user` | Private chat identity, activity, retention timestamps |
| `subscription` | Semester-scoped course and section watches |
| `notification_batch` | Exact channel-reported snapshot pair and lifecycle |
| `batch_recipient`, `batch_subscription` | Recipients and targets frozen at staging |
| `delivery` | Retry, lease, chunk progress, terminal state |

Completed batches are kept 90 days. Inactive users without subscriptions are
removed after 180 days; subscribed users stay until they clear or delete their data.

### Notification delivery

Personal alerts describe the same snapshot pair as the channel report, so they are
coupled to it:

- A batch is staged before the channel send. After the channel send succeeds and
  the reporting-log row is written, the batch becomes deliverable.
- Recipients and subscription row IDs are frozen at staging; later subscriptions,
  including unsubscribe and re-subscribe, never receive that batch.
- A pending batch is recovered only if it has its own channel-success marker and the
  exact current snapshot has a reportable reporting-log row.
- Digests filter the existing snapshot comparison with the shared reportability
  rules. A course watch supersedes overlapping section watches.
- A channel failure produces no digest. `--no-telegram` and debug runs create no
  batches. Unsubscribing before dispatch skips queued work.
- Delivery is retryable and restart-safe at chunk boundaries. If Telegram accepts a
  message but the process crashes before SQLite records it, the last chunk may be
  sent again; durable progress is favored over exactly-once.
- The bot receives updates by webhook through a Cloudflare Tunnel and runs as its
  own process, separate from the scheduler, so it can be
  operated or removed without touching enrollment history. The Mini App writes
  through the same store functions as the chat commands.

## Runtime configuration

`settings.toml` and `.env` load once through pydantic-settings
(`registrarmonitor.config`). Invalid configuration fails at startup; secrets are
`SecretStr` values read only from the environment. `get_settings()` gives typed
access.

The scheduler derives a `Schedule` from milestones in `settings.toml` and a small
`Cadence` state machine from tier tables (quiet 5/15/30 minutes with backoff, burst
down to 10 seconds, capped inside registration windows). A poll that finds the
registrar file unchanged skips parse, compare, and publish but still counts as
healthy and pings the heartbeat.

## Analysis

SQLite stays the write path. `analysis.py` attaches the per-semester databases to
DuckDB read-only and exposes cross-semester views; saved queries live in `analysis/`.

## Static dashboard read model

The dashboard has no backend. Each semester has a stable pointer
`data/<semester>/manifest.json` (`Cache-Control: no-cache`) naming the current and
previous immutable manifests `data/<semester>/manifests/<build-id>.json`. Manifests
reference canonical JSON blobs addressed by SHA-256, `data/blobs/<sha256>.json`;
manifests and blobs are cached for a year as `immutable`.

A manifest holds `manifestVersion`, `dataModelVersion`, `buildId`, `semester`,
`generatedAt`, `currentSnapshot` (id, `observedAt`, `overallFill`), a `summary`
reference, and one reference (url, sha256, bytes) per department. The summary blob
has current rows, aggregates, milestones, and recent-event previews, with no
histories. A department blob has current detail, compact stepped histories, events,
retired entities, and a local timestamp table. The browser fetches a department blob
when its first course opens and reuses it afterwards.

Publication order is blobs, then the versioned manifest, then the pointer. An
unchanged build writes no blobs and leaves the pointer byte-identical. Cloudflare
versions each deploy atomically, so rollback is `wrangler rollback`. Size targets: manifest and
pointer under 10 KB, summary under 100 KB, each department blob under 150 KB.

## Decisions worth remembering

- SQLite stays; one database per semester isolates identity, replication, and rollback.
- Fill, status, and averages are derived, not stored.
- Latest-state tables plus checkpoints keep reads simple and replay bounded;
  pure event sourcing and full snapshots per change were rejected.
- No dynamic web backend for the dashboard; static output served as Worker Static
  Assets is intentional: asset requests are free and never invoke the Worker, so
  traffic cannot exhaust the free plan's daily Worker-invocation cap. Only
  `/preview/*` runs code.
- The Telegram Mini App API is validated in the bot process, with no Worker in the
  path. The bot already owns the token (needed to verify `initData`) and the bot
  store, so a Worker would only proxy to it, cost an invocation per call, and need
  a second credential path to the store. The bot serves `/miniapp/*` next to its
  webhook from one small ASGI app (python-telegram-bot's custom-webhook pattern),
  behind the same Tunnel hostname; the user always comes from verified `initData`.
- One monolithic semester JSON and one blob per course were rejected in favor of
  department-level blobs.
