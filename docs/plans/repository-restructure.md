---
status: proposed
updated: 2026-11-05
scope: production rollout, runtime optimization, optional VM shrink
---

# Registrar Monitor restructure

Stages 1 through 8 are in the code and running in production at `seatwatch.fyi`. What
is left is below: production steps, the runtime work, and the optional Stage 9. This
is the only plan: no ADRs or separate specs. Delete steps as they are done, and the file when all are. It is a
personal project: fewer files, no evidence docs, tests only for behavior users
notice, docs describe the current system, and every production mutation (VM sync,
Worker deploy, database change, service state) needs explicit authorization.

## Remaining production steps

- Delete the archived databases and old `output/` from
  `~/backups/archived-20261104` on the VM (Decisions 1 and 4), after copying them off
  if they are worth keeping. Also delete the pre-rollout copies in
  `~/backups/pre-seatwatch-20261104`.
- Finalized databases still carry an unused `storage_control` table; drop it or leave
  it.
- Review the billing report for egress and IP SKUs after the next registration period.
- Add the next semester to `settings.toml` (`registrar_url` with its `termid`,
  milestones, deadlines). Fall 2026 is the newest configured semester and all 46
  milestones are past, so a scheduler started today polls the wrong term and stays out
  of every hot window.
- Install the new scheduler unit, then start the scheduler for the next registration
  period, and confirm the first Healthchecks.io ping. A scratch rehearsal on 2026-11-05
  already confirmed the download through the `ca_bundle`, parsing, storage, cadence,
  and report generation.
- Before that first start, decide how to handle the catch-up: the rehearsal's first
  cycle produced a change report of about 12.6 KB, which a real start would post to
  the channel, and the new snapshot would queue digests for subscribers. See "Baseline without
  posting" below.
- Optional: queued delivery (a Cloudflare Queue consumer honoring `RetryAfter`) only
  if digests grow.

## Runtime inspection and optimization

Goal: the VM stays healthy through a registration burst, the poller and bot are never
starved by a site build, and a one-course change publishes quickly. Findings come from
reading the code and from the 2026-11-05 rehearsal; only the numbers below were
measured. Measure first, then change only what the numbers justify.

**Measured so far**

| What | Result |
|---|---|
| `monitor deploy --force` for Fall 2026 (3.5 MB database) | over 4 CPU minutes on the VM, about 23% of RAM |
| wrangler upload | 4,474 asset files reported, 3,033 found by `find`; unexplained |
| Poller, five downloads and one report | 68 MB peak RSS |
| Registrar file | 788 KB; re-downloaded and re-parsed on every poll, no 304 or matching SHA-256 |
| Five polls with identical data | one new snapshot row: storage deduplicates correctly |

### 1. Measure

- Profile generation with cProfile or py-spy on a local copy of the Fall 2026 database,
  and record time and peak RSS per phase.
- Log duration and peak RSS per generation, poll, and digest batch, so a registration
  period gives real numbers.
- After the next period, read R2 write operations and storage for Litestream, GCP
  egress, and disk growth. The values in `scripts/litestream.yml` are still estimates.

### 2. Fix before the next registration period

- **Downloads are never deleted.** Nothing prunes `data/downloads`. At the 10-second
  burst rate that is up to about 280 MB an hour on a small disk. Delete each file after
  a successful parse, or keep the last N. `data/changes` also grows forever:
  `ReportingService.cleanup_old_reports` exists but is never called.
- **`skip_unchanged` never fires.** The rehearsal downloaded the file four times with
  the same size and data, and each one passed both checks (`automation/downloader.py:130`).
  The bytes appear to differ per response; confirm what changes (likely an embedded
  timestamp). Then compare a hash of the parsed rows, so an unchanged file skips the
  rest of the poll.
- **No memory limits.** `scripts/setup_vps.sh` sets no `MemoryMax`, `OOMScoreAdjust`, or
  `Nice`. The generation child and `npx wrangler` run inside the poller's cgroup
  (`automation/scheduler.py:331`), so an out-of-memory kill during a build can take the
  poller with it, at the moment polling matters most. Run the build in its own
  `systemd-run` scope with a memory cap and low CPU and I/O priority, and cap the
  poller and bot.
- **Redirect cap fails the whole publish.** `_build_redirects` raises above 2,000
  redirects (`services/website_service.py:538`), with one per course page across all
  semesters, so a new semester can stop all deploys. Drop the one-time `.html` course
  redirects, or warn instead of raising.
- **Checksum race.** The semester hash is computed after generation
  (`services/website_service.py:174`), so a snapshot stored during a 4-minute build is
  recorded as published. Compute it before reading the data. Also include milestones
  and archived state, which it currently ignores (`website/checksums.py`).

### 3. Site generation

Incremental generation is the target: one changed course should not rebuild everything.

- `website/data.py:302` reconstructs a full snapshot for every stored snapshot, and
  only then applies the window filter. Then it re-sorts and diffs every course and
  section. This is the likely main cost: read events from `section_change_event` and
  histories with one grouped query, or at least filter before reconstructing.
- `instructor_identity()` tokenizes both names for every unchanged section pair
  (`website/data.py:367`). Compare the raw strings first.
- `Course.sections` is an `ObservableDict` that fires a callback on every set
  (`models.py:84`), rebuilt for each snapshot. Use a plain dict.
- `build_semester_page` re-parses the Vite manifest for every course page
  (`website/templates.py:105`), and pages are rewritten even when unchanged. Render
  the shared part once and skip identical writes.
- Reuse a course's preview when a digest of its payload is unchanged
  (`website/preview.py:74`). This is the basis for incremental generation.
- `website/catalog.py:49` fully reconstructs each semester's latest snapshot to learn
  whether it can be published. Use an `EXISTS` query.
- `_prune_unreferenced_publication_files` joins all HTML into one string
  (`services/website_service.py:586`), and the tree is re-read and re-validated up to
  three times per run. Stream the files and validate once.
- Move the build to CI (Stage 9) if it still competes with the poller after this. Move
  read-model builders to DuckDB only where clearly simpler or faster.

### 4. Poller and data layer

- One poll runs `detect_active_semester`, which opens every semester database, up to
  three times, and builds about four services. It also reconstructs the same two
  snapshots twice (`cli/commands.py:128`, `automation/scheduler.py:377`). Pass one
  `DatabaseManager` and the snapshots through the poll.
- Excel parsing and the SQLite write run synchronously inside async code
  (`services/monitoring_service.py:240`), blocking the loop. Use `asyncio.to_thread`.
- Enrollment connections set no journal mode, `synchronous`, or cache size
  (`data/checkpointed_state.py:107`, `data/database_manager.py:122`). Set WAL and
  `synchronous=NORMAL` explicitly, so behavior does not depend on Litestream.

### 5. Bot

- Each delivery reconstructs two snapshots and a full comparison per recipient
  (`subscriptions/dispatcher.py:103`, `:210`). Cache by batch.
- The idle delivery loop polls the store every second (`subscriptions/runtime.py:474`),
  on a fresh connection, and the store uses `BEGIN IMMEDIATE`. Wake it with an event when
  a batch activates, or back off when idle.
- Every Mini App request finds the active semester across all databases and writes
  through `touch_user` (`subscriptions/runtime.py:417`, `subscriptions/miniapp.py:114`).
  Cache the semester for about 30 seconds and touch at most every few minutes.
- The bot store opens a connection per call without `synchronous=NORMAL`
  (`subscriptions/store.py:26`), so each commit fsyncs on a slow disk.

### 6. Deployment

- `make deploy-vm` uses plain `ssh` (untested here; gcloud's key and known-hosts
  differ), syncs dependencies while services are live, checks `is-active` without
  waiting for health, restarts the one unit it names, and starts it if it was stopped.
  Split sync from restart, add a health wait, and never start a stopped scheduler.
- `uv run` adds start-up cost and can sync; use `.venv/bin/monitor` in every unit.
- `npm ci` resets `frontend/node_modules` ownership, and the mtime-based `npm install`
  could fire as the runtime user.
- `scripts/fetch_registrar_ca.sh` hides `s_client` failures, and the pinned bundle must
  be refreshed when the chain changes. Have `monitor doctor` warn before expiry.
- The Healthchecks check alerts while the scheduler is deliberately stopped. Pause and
  resume it as part of the period start and stop.
- Clean up: the stale `scripts/registrarmonitor-health.service` in the VM's git index,
  and the unused `registrar-monitor-preview-images`, `monitor`, and `noisy-water-fd35`
  Workers (list first, delete on confirmation).

### 7. Small features and cleanups

- **`monitor rehearse`:** the rehearsal used a throwaway script. Make it a command: a
  scratch copy of the database, credentials cleared, deploy disabled, and a check that
  live data did not change.
- **Baseline without posting:** a command that marks the latest snapshot as reported
  after a long stop, so the first start does not post months of changes.
- **`monitor deploy --dry-run`** and progress output: `--force` is silent for minutes.
- **`monitor export-parquet`** now has no consumer. Decide whether to keep it, and
  whether `monitor analyze` justifies the DuckDB dependency on the VM.
- **`workers_dev: true`** leaves a second public origin; turn it off, and use the Cache
  API for previews now that the custom domain supports it.
- **Check apex DNS from carriers:** one hotspot resolver returned no address for
  `seatwatch.fyi`. If it recurs for users, serve the Mini App and links from `www`.
- `workers/site/worker-configuration.d.ts` is rewritten by `wrangler types`; generate
  it consistently so it stops showing up as a diff.

**Done when** a burst rehearsal shows the poller and bot unaffected by a concurrent
build, a one-course change publishes in well under the current 4 minutes, disk use is
flat across a registration period, and the billing review is within free limits.

## Stage 9 — Optional: shrink or leave the VM

Do this after the production rollout above, and only if VM upkeep still hurts.

**Constraint.** Burst mode polls as often as every 10 seconds. Cron Triggers
fire at most once a minute, so they cannot replace the scheduler.

| Option | Cadence | Keeps Python | Cost |
|---|---|---|---|
| Minimal VM running only the poller (**recommended**) | seconds | yes | GCP `e2-micro` free tier is expected to suffice; verify under burst load |
| Durable Object alarm waking a Cloudflare Container running the poller | seconds | yes | SQLite must live on R2 (Litestream required); container warm during bursts |
| Durable Object alarm driving a Python Worker | seconds | partly (Pyodide; `xlrd` is pure Python) | Storage moves to DO SQLite or D1: full data-layer rewrite |

After the rollout, the VM runs the poller, the bot (webhook and Mini App API behind
the Tunnel, with its SQLite store), and Litestream; backups are on R2, the heartbeat is
external, and the site and previews are on the Worker. The VM also generates and
deploys the site (Node and wrangler); moving that to CI is one step toward a
poller-only VM. The bot is the other: it stays on the VM while it owns the store and
the Mini App API (see below). The network watchdog stays: it is generic VM network
recovery, not tied to polling.

**Test before choosing a Cloudflare option:**

- **TLS.** The registrar chain needs the pinned `ca_bundle`, and Worker `fetch`
  cannot use one or skip verification, so Workers cannot poll; only a Container
  or VM can.
- **Reachability.** The registrar must accept 10-second polling from
  Cloudflare egress IPs without blocking.

**Moving the bot fully to Workers** (webhook Worker, D1 bot store, Queue for
digests) is possible but means rewriting about 2.5k lines of
subscription code in TypeScript. Do it only together with leaving the VM
entirely.

**Telegram Serverless** (`core.telegram.org/bots/serverless`, CLI 0.2.0,
October 2026) runs handlers and Mini App endpoints on Telegram's
infrastructure, with a managed webhook and a per-bot SQLite database. It
fits commands, callbacks, and a Mini App backend. It is not adopted because:

- there is no scheduler and no documented external trigger, so the poller
  cannot start digests;
- there is no documented way for the poller to read subscriptions;
- it is JavaScript only, with no npm packages and no foreign keys;
- pricing and limits are not published.

Re-evaluate if Telegram adds scheduled triggers or authenticated
server-to-server calls. The split would then be: interactions on Telegram
Serverless, digests triggered by the poller.

## Decisions

1. **Unconfigured databases** (`data/enrollment.db`, 2024 semesters,
   `data/recovered_variants/`): compress into one archive outside the
   repository, then delete from `data/`.
2. **Unreferenced scripts:** delete; git history keeps them.
3. **Prototype dashboard:** delete. Wanted ideas become issues against the main
   dashboard.
4. **`output/`:** delete everything; optionally keep the five
   `checkpointed-state-archive-*` files in the off-repo archive from (1).
5. **GitHub labels:** delete the obsolete workflow and unused triage labels; list them
   first and delete only after confirmation.
6. **VM:** keep a minimal VM as the poller (see Stage 9 for why).
7. **Telegram Serverless:** not adopted yet (see Stage 9).
8. **Site data on Static Assets, not D1/R2 behind Worker routes** (Stage 7): asset
   requests are free and unlimited, while Worker invocations are capped at 100,000
   per day on the free plan and the Cache API does nothing on `workers.dev`.
9. **Telegram Mini App served by the bot, no Worker in the path** (Stage 7): the bot
   owns the token and the store, so it validates `initData` and writes directly,
   behind the existing Tunnel; zero Worker invocations. Sentry for the Worker was
   skipped: add it to `workers/site` if the Worker gains more than the preview route.
10. **Invariant:** VM SQLite stays the source of truth; the deployed site is a
    rebuildable copy. The Worker is a runtime dependency of the dashboard.

## Tool choices

| Area | Chosen | Alternatives weighed | Why |
|---|---|---|---|
| Worker routing | Plain `fetch` handler | Hono, itty-router, Worktop | With data on Static Assets and the Mini App API on the bot, the Worker has only `/preview/*`; a middleware router has nothing to do. |
| Mini App server | Starlette under uvicorn, in the bot process | PTB's built-in tornado webhook, aiohttp, FastAPI | The built-in webhook server cannot add routes. Starlette plus uvicorn is what python-telegram-bot's custom-webhook example uses, and it covers CORS and routing without pydantic models. |
| Charts and linting | Keep **Chart.js** and **ESLint** | uPlot, ECharts; Biome | Evaluated, skipped. uPlot is about 50 KB versus about 250 KB, but lacks the annotation and zoom plugins the dashboard relies on; ECharts is about 1 MB. Biome would replace a working ESLint setup for no user-visible gain. |
| XLS parsing | Keep **xlrd** | python-calamine (Rust) | Calamine is about 1.5x faster in pandas' general benchmarks, but scores lower on `.xls` read fidelity in ExcelBench (2/4 versus 4/4). One 770 KB file per poll does not need the speed; fidelity matters more. Revisit only if parsing shows up in profiles. |

## Considered and rejected

- **htmx + Hono server rendering:** data changes only when a poll runs and
  every visitor sees the same thing, so prebuilt output fits better; the
  Chart.js code would stay, and search would become a network round trip.
- **Postgres or another server database:** each semester's database is 3 MB or
  less.
- **Microservices or an event bus:** the Worker split gives enough separation.
