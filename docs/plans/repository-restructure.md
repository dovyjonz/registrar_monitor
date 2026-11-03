---
status: proposed
updated: 2026-11-03
scope: cleanup, agent instructions, Cloudflare publication, optional VM shrink
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
frontend/               (Vite + Chart.js dashboard source)
GCP VM                  (scheduler, bot, Cloudflare Tunnel, Litestream, watchdog)
```

The package boundaries are sound. The remaining problems are leftovers, scattered
agent instructions, and a publish path that regenerates and uploads the whole site.

| Finding | Evidence | Stage |
|---|---|---|
| Migration machinery outlives the migration | `data/migration.py` (2.7k lines), `migration_rehearsal.py`, CLI `migrate`/`finalize-storage`/`rehearse-migration`, `[storage]` in `settings.toml`, shadow paths in `checkpointed_state.py`; every semester is finalized | 1 |
| Prototype dashboard is a second UI | `prototype.{js,css}`, `prototype.html.jinja`, `deploy --prototype` | 1 |
| Local debris, unused scaffolds, dated evidence docs | See Stage 1 | 1 |
| Agent instructions are scattered and carry Matt Pocock skill wiring | `AGENTS.md`, `docs/agents/`, `opencode.json` | 4 |
| Every publish regenerates and uploads ~1,200 files | `static_manifest.py`, `website_service.py` (~2k lines), 3,600 s generation limit | 7 |

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
| 4 | Consolidate agent instructions | S | 1 |
| 7 | Publish data through a Worker | L | Litestream/R2 (landed) |
| 9 | Optional: shrink or leave the VM | L | 7 |

Stages 1 and 4 change no behavior. Each stage is one or a few jj changes, each
landing with `make check` green. Stages 5, 6, and 8 are in the code; their
production steps are under "Remaining production steps".

---

### Stage 1 — Delete dead files and code (code landed; production steps remain)

Code, docs, scripts, migration machinery, and the prototype dashboard are gone.
What remains needs the VM or a decision:

- Archive and delete the unconfigured databases (Decision 1) and `output/`
  (Decision 4). The code now rejects any non-v2 database file it opens.
- Run the doctor against every live database after deploying.
- Finalized databases still carry an unused `storage_control` table; drop it
  or leave it.

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

### Stage 7 — Publish data through a Worker

- After each poll, the VM pushes only changed state to Cloudflare: rows to
  **D1**, or the compact per-semester database to **R2** (Litestream already
  replicates the databases there).
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

**Sentry for the Worker was skipped:** this stage replaces the preview Worker, so add
Sentry to the new Worker rather than the old one.

**Invariant:** VM SQLite stays the source of truth; D1 and R2 are read replicas
rebuilt from it. The Worker becomes a runtime dependency of the dashboard.

### Stage 9 — Optional: shrink or leave the VM

Do this after Stage 7, and only if VM upkeep still hurts.

**Constraint.** Burst mode polls as often as every 10 seconds. Cron Triggers
fire at most once a minute, so they cannot replace the scheduler.

| Option | Cadence | Keeps Python | Cost |
|---|---|---|---|
| Minimal VM running only the poller (**recommended**) | seconds | yes | GCP `e2-micro` free tier is expected to suffice; verify under burst load |
| Durable Object alarm waking a Cloudflare Container running the poller | seconds | yes | SQLite must live on R2 (Litestream required); container warm during bursts |
| Durable Object alarm driving a Python Worker | seconds | partly (Pyodide; `xlrd` is pure Python) | Storage moves to DO SQLite or D1: full data-layer rewrite |

After Stage 7, a poller-only VM has little left on it: the bot already uses
webhooks through a Tunnel, backups are on R2, and the heartbeat is external; the site
and previews move to the Worker. The network watchdog stays: it is generic VM network
recovery, not tied to polling.

**Test before choosing a Cloudflare option:**

- **TLS.** The registrar chain needs the pinned `ca_bundle`, and Worker `fetch`
  cannot use one or skip verification, so Workers cannot poll; only a Container
  or VM can.
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

## Remaining production steps

Code for the stages below has landed. Each step needs explicit operator
authorization.

**Stage 5, VM runtime**

- Run `scripts/fetch_registrar_ca.sh` on the VM before deploying, so the
  `ca_bundle` exists.
- Create the R2 bucket and Litestream keys, install `litestream` and its unit, and
  test a restore.
- Create a Healthchecks.io check with its Telegram integration and set
  `HEARTBEAT_URL`.
- Set `SENTRY_DSN`.
- Remove the old `registrarmonitor-health.service` unit.

**Stage 6, Telegram**

- Create the `cloudflared` tunnel and config, install the tunnel unit, and set
  `TELEGRAM_WEBHOOK_URL` and `TELEGRAM_WEBHOOK_SECRET`; deploy the bot unit after it.
- Optional: a thin Worker in front of the tunnel to reject malformed requests.
- Queued delivery (a Cloudflare Queue consumer honoring `RetryAfter`) only if digests
  grow.

**Stage 8, analysis**

- Upload the Parquet exports to R2 next to the dashboard (needs Stage 7).
- Optional: an "Explore data" page using DuckDB-WASM over the exports.
- Optional: move website read-model builders to DuckDB only where clearly simpler or
  faster.

## Tool choices evaluated (2026-11-01)

| Area | Chosen | Alternatives weighed | Why |
|---|---|---|---|
| Worker routing (Stage 7) | **Hono** | Plain `fetch` handler (what `workers/preview-images` uses today), itty-router, Worktop | Stage 7 adds ~8 routes plus Mini App `initData` auth, ETag/cache, and CORS middleware; Hono provides those built in, has zero dependencies, and tests via `app.request()` without a runtime. itty-router is smaller (<1 KB) but has no middleware ecosystem; Worktop is effectively superseded. Router speed is irrelevant because the runtime dominates. If Stage 7 ends up with only data and preview routes, keep the plain `fetch` handler instead. |
| Charts (Stage 7, optional) | Keep **Chart.js** | uPlot, ECharts | uPlot is about 50 KB versus about 250 KB, with much lower CPU and memory on long time series, but it lacks built-in annotation and zoom plugins that the dashboard relies on. ECharts is about 1 MB. Switch only if the chart code is being rewritten anyway; prototype the annotation, pinch-zoom, and pinned-readout behavior first. |
| XLS parsing | Keep **xlrd** | python-calamine (Rust) | Calamine is about 1.5x faster in pandas' general benchmarks, but scores lower on `.xls` read fidelity in ExcelBench (2/4 versus 4/4). One 770 KB file per poll does not need the speed; fidelity matters more. Revisit only if parsing shows up in profiles. |

Correction to the earlier review: `workers/preview-images` uses a plain
`fetch` handler, not Hono.

## Considered and rejected

- **htmx + Hono server rendering:** data changes only when a poll runs and
  every visitor sees the same thing, so prebuilt output fits better; the
  Chart.js code would stay, and search would become a network round trip.
- **Postgres or another server database:** each semester's database is 3 MB or
  less.
- **Microservices or an event bus:** Stage 7 gives enough separation.
