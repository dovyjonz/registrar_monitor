---
status: proposed
updated: 2026-11-01
scope: cleanup, layout, testing, agent instructions, runtime, Telegram, Cloudflare, analysis
---

# Registrar Monitor restructure

One spec for all planned structural work, split into stages that can be
implemented independently and later. This file is the only plan; there are no
ADRs or separate specs. Delete each stage's section once it lands, and delete
the file when the last one does.

## Principles

This is a personal project. Every stage is judged by these:

- **Fewer files.** One document over several; delete rather than archive
  in-tree. A file stays only if code, CI, or a person reads it regularly.
- **No bike-shedding.** No ADRs, evidence ledgers, dated baselines, or
  sign-off records. Decide, write the code, and let the commit message explain
  why.
- **Tests guard behavior users notice.** No tests for timing, file layout,
  script text, or deleted paths.
- **Docs describe the current system only.** History lives in git.
- **Production is separate.** VM sync, Pages upload, Worker deploys, database
  changes, and service state each need explicit authorization, in every stage.

## Current system and findings

```text
registrar XLS → automation/ → data/ (per-semester SQLite, v2 finalized)
                                 ├─ services/reporting → reporting/ → Telegram channel
                                 │                    └─ subscriptions/ (bot DB, digests)
                                 └─ services/website → website/ → static site → Pages
workers/preview-images  (separate Worker, /preview/*)
assets/website          (Vite + Chart.js dashboard source)
GCP VM                  (scheduler, bot, health monitor, watchdog)
```

The package boundaries are sound. The problems are leftovers, weight, and a VM
that does more than it needs to.

| Finding | Evidence | Stage |
|---|---|---|
| Migration machinery outlives the migration | `data/migration.py` (2.7k lines), `migration_rehearsal.py`, CLI `migrate`/`finalize-storage`/`rehearse-migration`, `[storage]` in `settings.toml`, shadow paths in `checkpointed_state.py`; every semester is finalized | 1 |
| Prototype dashboard is a second UI | `prototype.{js,css}`, `prototype.html.jinja`, `deploy --prototype` | 1 |
| Local debris, unused scaffolds, dated evidence docs | See Stage 1 | 1 |
| Test suite (17.5k lines) is close to source size (22k); CI has seven jobs | `tests/`, `.github/workflows/ci.yml` | 2 |
| `main.js` (3.5k lines) is the most-changed file | last three months of history | 3 |
| Docs are spread over 20+ files | `docs/`, `DATABASE.md`, `CONTEXT.md` | 3 |
| Agent instructions are scattered and carry Matt Pocock skill wiring | `AGENTS.md`, `docs/agents/`, `opencode.json` | 4 |
| Registrar TLS verification is disabled | `allow_insecure_tls = true` | 5 |
| Backups and health checks are manual or self-hosted | `backup-and-recovery.md`, `registrarmonitor-health.service` | 5 |
| `scheduler.py` (1.5k lines) and ad hoc settings parsing | two TOML libraries in use | 5 |
| Bot long-polls from the VM | `registrarmonitor-bot.service`, network watchdog | 6 |
| Every publish regenerates and uploads ~1,200 files | `static_manifest.py`, `website_service.py` (~2k lines), 3,600 s generation limit | 7 |
| Cross-semester questions turn into one-off scripts | deleted scripts in Stage 1 | 8 |

## Decisions (resolved 2026-11-01)

1. **Unconfigured databases** (`data/enrollment.db`, 2024 semesters,
   `data/recovered_variants/`): compress into one archive outside the
   repository, then delete from `data/`.
2. **Unreferenced scripts:** delete; git history keeps them.
3. **Prototype dashboard:** delete. Wanted ideas become issues against the main
   dashboard.
4. **`output/`:** delete everything; optionally keep the five
   `checkpointed-state-archive-*` files in the off-repo archive from (1).
5. **GitHub labels:** delete `wayfinder:*` and unused triage labels; list them
   first and delete only after confirmation.
6. **VM:** keep a minimal VM as the poller (see Stage 9 for why).
7. **Telegram Serverless:** not adopted yet (see Stage 9).

## Stages

| Stage | Theme | Size | Depends on |
|---|---|---|---|
| 1 | Delete dead files and code | M | — |
| 2 | Cut testing, CI, and process | M | 1 |
| 3 | Reorganize layout and docs | M–L | 1 |
| 4 | Consolidate agent instructions | S | 3 |
| 5 | Harden the VM runtime | M | — |
| 6 | Telegram webhooks and deep links | M | 5 (Tunnel, heartbeat) |
| 7 | Publish data through a Worker | L | 3, 5 (Litestream/R2) |
| 8 | DuckDB analysis and open data | S–M | 7 for public export |
| 9 | Optional: shrink or leave the VM | L | 5, 6, 7 |

Stages 1–4 change no behavior. Each stage is one or a few jj changes, each
landing with `make check` green.

---

### Stage 1 — Delete dead files and code (code landed; production steps remain)

Code, docs, scripts, migration machinery, and the prototype dashboard are gone.
What remains needs the VM or a decision:

- Archive and delete the unconfigured databases (Decision 1) and `output/`
  (Decision 4). The code now rejects any non-v2 database file it opens.
- Run the doctor against every live database after deploying.
- `scripts/manage_database.py` was kept: it is the documented online-backup
  tool (`DATABASE.md`, `tests/test_database_backup.py`). Fold it into Stage 5
  backups or keep it.
- Finalized databases still carry an unused `storage_control` table; drop it
  or leave it.

### Stage 2 — Cut testing, CI, and process

**Tests**

- Remove `test_performance_benchmark.py` and all timing budgets,
  `test_systemd_setup.py` and `test_runtime_doctor.py` string checks; merge the
  unique cases of `test_website_service_extended.py` into
  `test_website_service.py`.
- Keep: storage reads and reconstruction, report → batch → digest, report
  formatting, static data pipeline, CLI smoke, frontend `.mjs` units, Worker
  units.
- Trim `browser-smoke.spec.mjs` (1.6k lines) to about 10 journeys: load,
  search, modal open/close, chart renders, share route, mobile layout. Drop the
  WebKit-only and repeated-run stability gates; Chromium only.
- Remove `--cov-fail-under` and the default `htmlcov` report.

**CI**: three jobs instead of seven.

1. Python 3.13: ruff format and lint, ty, pytest. Drop the 3.14 job.
2. Frontend and Worker: lint, unit tests, build, Worker typecheck.
3. Browser smoke on generated output, only for changes under the frontend or
   `website/`.

Drop the benchmark and dependency-review jobs. Keep Dependabot, grouped and
monthly.

**Process**

- Delete `docs/baselines/`, `scripts/write_baseline.py`,
  `scripts/benchmark_performance.py`, and the `baseline`, `benchmark-*`,
  `benchmark-record*`, and `release-candidate` targets.
- Makefile keeps: `bootstrap`, `check-fast`, `check`, `test-browser`,
  `site-generate`, `worker-check`, `clean`.
- Pre-commit keeps ruff and the private-key check; CI runs the rest.
- Production verification goes in the commit message, not a document.

**Done when** CI finishes in under five minutes and the suite is roughly half
its current size with the kept seams covered.

### Stage 3 — Reorganize layout and docs

```text
registrar_monitor/
├── AGENTS.md            single agent entry point (Stage 4)
├── CLAUDE.md            one line: @AGENTS.md
├── README.md            setup and commands only
├── settings.toml
├── docs/
│   ├── architecture.md  pipeline, storage (DATABASE.md), glossary (CONTEXT.md),
│   │                    lasting decisions from both ADRs
│   ├── operations.md    production, publication, Telegram, backup, tooling
│   └── plans/repository-restructure.md   until done
├── frontend/            was assets/website
├── workers/preview-images/
├── scripts/             only what Makefile, CI, or VM setup call
├── src/registrarmonitor/
└── tests/
```

- Move `assets/website/` to `frontend/`; update Makefile, CI, Vite,
  Playwright, `WebsiteService`, and settings paths. Move any remaining runtime
  inputs from `assets/` to `data/` and delete `assets/`.
- Merge `DATABASE.md`, `CONTEXT.md`, and `docs/adr/` into
  `docs/architecture.md`; merge `docs/operations/` into `docs/operations.md`,
  describing current state only (no per-maintenance narratives).
- Split `main.js` by concern (`chart`, `modal`, `routing`, `state`,
  `search`), keeping the existing `.mjs` helpers; add `// @ts-check` with JSDoc
  types. TypeScript is optional later; no component framework.
- Replace the `toml` package with stdlib `tomllib`; drop `types-toml` from
  runtime dependencies.
- Check whether the PDF report (`fpdf`, `pdf_generator.py`) is still sent
  anywhere; delete it if not.

**Done when** `make check`, `make worker-check`, and the browser smoke pass,
and no document or skill names a removed path.

### Stage 4 — Consolidate agent instructions

End state: the only agent-facing files are `AGENTS.md`, `CLAUDE.md`
(`@AGENTS.md`), and four skills in `.agents/skills/`.

- Delete `docs/agents/`. Move its `gh` issue commands into `AGENTS.md`; drop
  the rest.
- Remove every Matt Pocock artifact: `/ask-matt`, `/domain-modeling`,
  Wayfinding, `wayfinder:*` labels, and the triage-role mapping. Labels on
  GitHub follow Decision 5.
- Rewrite `AGENTS.md` in at most 90 lines:
  1. What the project is, and the pipeline diagram.
  2. Sources of truth: `settings.toml`, `Makefile`, `monitor --help`,
     `docs/architecture.md`.
  3. Invariants (current list plus "v2 schema only").
  4. Package boundaries.
  5. Commands: setup; one test (`uv run pytest tests/test_x.py -k name`,
     `node --test frontend/test/x.test.mjs`, `npm test` in the Worker);
     `check-fast`; `check`; which targets need network or browsers.
  6. Verification: focused test, then `make check-fast`; browser tests only when
     UI behavior changed; no evidence documents.
  7. Version control: jj rules, unchanged.
  8. Registrar calendar: when production is live and when changes are safe.
  9. Skills: one line each on when to load them.
  10. Docs rule: no ADRs or separate specs; lasting decisions go in
      `docs/architecture.md`.
- Skills:
  - **storage:** "v2 is the only schema; schema changes are forward-only
    migrations in code" replaces the migration and finalization text.
  - **dashboard:** new paths; add a short Worker section; move the chart-radius
    rule into a code comment.
  - **production, telegram:** point to `docs/operations.md`; content otherwise
    unchanged.

**Done when** `git grep -iE 'matt|wayfinder|ask-matt'` returns nothing and
every path a skill names exists.

### Stage 5 — Harden the VM runtime

Each item stands alone.

- **Fix registrar TLS.** Pin the registrar's intermediate certificate in a
  `ca_bundle` and remove `allow_insecure_tls`. This also decides whether
  Workers could ever poll (Stage 9).
- **Skip unchanged downloads.** Hash each XLS, and use `ETag` or
  `Last-Modified` if sent; skip parse, compare, and publish when nothing
  changed.
- **Litestream to R2.** Continuous replication of every enrollment database and
  `telegram_bot.db`, with point-in-time restore; replaces the manual backup
  runbook.
- **External heartbeat.** After each successful poll, ping Healthchecks.io or a
  Cron-Triggered Worker that checks a heartbeat key in KV, and alert on
  Telegram when it goes quiet. Delete `registrarmonitor-health.service`.
- **Errors and logs.** Sentry (free tier) for Python and Workers; structlog or
  stdlib JSON logging.
- **Typed settings.** Pydantic Settings loads `settings.toml` and `.env` once,
  fails fast on invalid config, and holds secrets as `SecretStr`.
- **Simpler scheduler.** Replace custom timing logic in `scheduler.py` with a
  declarative schedule derived from the milestones in `settings.toml`. Keep the
  burst cadence (down to 10 seconds).
- **One-command deploy.** `make deploy-vm` syncs a named jj revision, runs
  `uv sync --locked`, restarts only the named unit, and prints the deployed
  revision. It still runs only when explicitly authorized.

### Stage 6 — Telegram webhooks and deep links

The bot stays in Python on the VM.

- **Webhooks instead of long polling.** python-telegram-bot in webhook mode
  behind a **Cloudflare Tunnel** (`cloudflared`): no open VM port, TLS at
  Cloudflare. Verify Telegram's `secret_token` header on every update.
  Optionally put a thin Worker in front to reject malformed requests and
  rate-limit. This removes the long-lived polling connection the network
  watchdog exists for.
- **One-tap watches from the channel.** Each course in a channel report links
  to `t.me/<bot>?start=watch_<semester>_<course>`; keep the confirmation step.
- **Queued delivery (only if digests grow).** A Cloudflare Queue consumer sends
  digests and honors `RetryAfter`; the batch tables stay the source of truth.

### Stage 7 — Publish data through a Worker

- After each poll, the VM pushes only changed state to Cloudflare: rows to
  **D1**, or the compact per-semester database to **R2** (Litestream from
  Stage 5 may already put it there).
- One **Hono** Worker (see "Tool choices") serves:
  - the dashboard shell via Workers Static Assets, replacing Pages;
  - the JSON the dashboard already fetches (`/api/semesters/:s/courses/:c`),
    edge-cached by state hash;
  - clean course routes and `/preview/*`, absorbing
    `workers/preview-images`.
- Delete most of `static_manifest.py` and the upload path in
  `website_service.py`.
- **Telegram Mini App.** Open the dashboard inside Telegram and add or remove
  watches from the course modal. The Worker validates `initData` and writes
  through to the bot store. Shrink the matching command flows in
  `interactions.py` (1.6k lines).
- **Small wins:** Cloudflare Web Analytics (cookieless); long-lived cache
  headers on content-addressed files.
- **Optional:** evaluate uPlot instead of Chart.js and its two plugins if chart
  code is being rewritten anyway; Biome instead of ESLint.

**Invariant:** VM SQLite stays the source of truth; D1 and R2 are read replicas
rebuilt from it. The Worker becomes a runtime dependency of the dashboard.

### Stage 8 — DuckDB analysis and open data

SQLite stays the write path. DuckDB reads the per-semester files directly
through its `sqlite` extension, read-only, with no copy or sync. One Python
dependency (`duckdb`).

- **Cross-semester analysis.** `ATTACH` every `enrollment_*.db` and query them
  as one data set: fill curves by department, fill speed after each
  registration gate, mid-registration capacity changes, year-over-year course
  comparisons.
- **`monitor analyze <name>`.** Runs `analysis/<name>.sql` against the attached
  databases and prints a table or writes CSV. Saved queries replace one-off
  scripts; the `duckdb` CLI works directly too.
- **Open data export.** `COPY (…) TO '<semester>.parquet'` writes a
  per-semester export, published to R2 next to the dashboard (needs Stage 7).
- **Read models.** Benchmark the per-course history and historical-comparison
  builders in `website/data.py` against DuckDB queries; move only those that
  become simpler or clearly faster.
- **Optional:** an "Explore data" page using DuckDB-WASM over the Parquet
  exports, with no server queries.

### Stage 9 — Optional: shrink or leave the VM

Do this after Stages 5–7, and only if VM upkeep still hurts.

**Constraint.** Burst mode polls as often as every 10 seconds. Cron Triggers
fire at most once a minute, so they cannot replace the scheduler.

| Option | Cadence | Keeps Python | Cost |
|---|---|---|---|
| Minimal VM running only the poller (**recommended**) | seconds | yes | GCP `e2-micro` free tier is expected to suffice; verify under burst load |
| Durable Object alarm waking a Cloudflare Container running the poller | seconds | yes | SQLite must live on R2 (Litestream required); container warm during bursts |
| Durable Object alarm driving a Python Worker | seconds | partly (Pyodide; `xlrd` is pure Python) | Storage moves to DO SQLite or D1: full data-layer rewrite |

After Stages 5–7, a poller-only VM has nothing else left on it: the bot uses
webhooks, the site and previews are on the Worker, backups are on R2, and the
heartbeat is external.

**Test before choosing a Cloudflare option:**

- **TLS.** Worker `fetch` cannot skip certificate verification. If only a
  pinned bundle fixes the registrar chain, Workers cannot poll; only a
  Container or VM can.
- **Reachability.** The registrar must accept 10-second polling from
  Cloudflare egress IPs without blocking.

**Moving the bot fully to Workers** (webhook Worker, D1 bot store, Queue for
digests) is possible after Stage 7 but means rewriting about 2.5k lines of
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

## Tool choices evaluated (2026-11-01)

| Area | Chosen | Alternatives weighed | Why |
|---|---|---|---|
| Worker routing (Stage 7) | **Hono** | Plain `fetch` handler (what `workers/preview-images` uses today), itty-router, Worktop | Stage 7 adds ~8 routes plus Mini App `initData` auth, ETag/cache, and CORS middleware; Hono provides those built in, has zero dependencies, and tests via `app.request()` without a runtime. itty-router is smaller (<1 KB) but has no middleware ecosystem; Worktop is effectively superseded. Router speed is irrelevant because the runtime dominates. If Stage 7 ends up with only data and preview routes, keep the plain `fetch` handler instead. |
| SQLite backups (Stage 5) | **Litestream** to R2 | LiteFS, periodic `sqlite3_rsync` or `.backup` + cron | Single-writer, single-host fits Litestream's intended use: continuous WAL shipping and point-in-time restore on any S3-compatible store. LiteFS solves multi-node replication, which this project does not need. A nightly `.backup` to R2 remains a cheap fallback. |
| Charts (Stage 7, optional) | Keep **Chart.js** | uPlot, ECharts | uPlot is about 50 KB versus about 250 KB, with much lower CPU and memory on long time series, but it lacks built-in annotation and zoom plugins that the dashboard relies on. ECharts is about 1 MB. Switch only if the chart code is being rewritten anyway; prototype the annotation, pinch-zoom, and pinned-readout behavior first. |
| XLS parsing | Keep **xlrd** | python-calamine (Rust) | Calamine is about 1.5x faster in pandas' general benchmarks, but scores lower on `.xls` read fidelity in ExcelBench (2/4 versus 4/4). One 770 KB file per poll does not need the speed; fidelity matters more. Revisit only if parsing shows up in profiles. |
| Telegram library (Stage 6) | Keep **python-telegram-bot** | aiogram | Both are async and support webhooks. Switching would rewrite 2.5k lines of subscription code for no user-visible gain. |
| Analysis (Stage 8) | **DuckDB** | Datasette, Polars, pandas | DuckDB attaches the SQLite files read-only and writes Parquet directly. Polars and pandas need loading code; Datasette is a browsing UI, not an export or analysis engine. |

Correction to the earlier review: `workers/preview-images` uses a plain
`fetch` handler, not Hono.

## Considered and rejected

- **htmx + Hono server rendering:** data changes only when a poll runs and
  every visitor sees the same thing, so prebuilt output fits better; the
  Chart.js code would stay, and search would become a network round trip.
- **Postgres or another server database:** each semester's database is 3 MB or
  less.
- **Microservices or an event bus:** Stage 7 gives enough separation.
