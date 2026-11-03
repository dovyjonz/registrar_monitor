---
status: proposed
updated: 2026-11-03
scope: cleanup, Cloudflare publication, optional VM shrink
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
- **Production is separate.** VM sync, Worker deploys (which publish the site), database
  changes, and service state each need explicit authorization, in every stage.

## Current system and findings

```text
registrar XLS → automation/ → data/ (per-semester SQLite, v2 finalized)
                                 ├─ services/reporting → reporting/ → Telegram channel
                                 │                    └─ subscriptions/ (bot DB, digests)
                                 └─ services/website → website/ → static site → wrangler deploy
workers/site            (Worker: Static Assets + /preview/*)
frontend/               (Vite + Chart.js dashboard source)
GCP VM                  (scheduler, bot, Cloudflare Tunnel, Litestream, watchdog)
```

The package boundaries are sound. The remaining problems are leftovers and a publish
path that regenerates and uploads the whole site.

| Finding | Evidence | Stage |
|---|---|---|
| Migration machinery outlives the migration | `data/migration.py` (2.7k lines), `migration_rehearsal.py`, CLI `migrate`/`finalize-storage`/`rehearse-migration`, `[storage]` in `settings.toml`, shadow paths in `checkpointed_state.py`; every semester is finalized | 1 |
| Prototype dashboard is a second UI | `prototype.{js,css}`, `prototype.html.jinja`, `deploy --prototype` | 1 |
| Local debris, unused scaffolds, dated evidence docs | See Stage 1 | 1 |
| Every publish regenerates ~1,200 files (uploads are now incremental) | `static_manifest.py`, `website_service.py` (~2k lines), 3,600 s generation limit | 7 |

## Decisions (resolved 2026-11-01)

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

## Stages

| Stage | Theme | Size | Depends on |
|---|---|---|---|
| 1 | Delete dead files and code | M | — |
| 7 | Telegram Mini App (the Worker and Static Assets part has landed) | M | Litestream/R2 (landed) |
| 9 | Optional: shrink or leave the VM | L | 7 |

Stage 1 changes no behavior. Each stage is one or a few jj changes, each
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

### Stage 7 — Telegram Mini App (site Worker landed)

**Landed:** one Worker (`workers/site`) replaces Pages. The dashboard, its JSON, and
the per-course pages are Workers Static Assets deployed with `wrangler deploy`
(wrangler uploads only changed files, so VM egress stays small); the Worker runs
code only for `/preview/*` and `/api/*` (`assets.run_worker_first`). Decision: data
stays on static assets rather than D1/R2 behind Worker routes, because asset
requests are free and unlimited while Worker invocations are capped at 100,000 per
day on the free plan and the Cache API does nothing on `workers.dev`. Hono was not
needed for two route families; add it with the Mini App if its middleware (auth,
CORS) pays off.

- **Telegram Mini App.** Open the dashboard inside Telegram and add or remove
  watches from the course modal. A Worker route under `/api/miniapp/*` validates
  `initData` and writes through to the bot store (reachable from the Worker, for
  example through the Stage 6 Tunnel). Shrink the matching command flows in
  `interactions.py` (1.6k lines).
- **Optional:** evaluate uPlot instead of Chart.js and its two plugins if chart
  code is being rewritten anyway; Biome instead of ESLint.

**Sentry for the Worker was skipped:** add it to `workers/site` if the Worker gains
more than the preview route.

**Production steps for the landed part (each needs authorization):** set
`CLOUDFLARE_API_TOKEN` (Workers Scripts edit) and `CLOUDFLARE_ACCOUNT_ID` in the VM
`.env`; sync the code and run `npm ci --prefix frontend` on the VM; `make
worker-check`; `monitor deploy --deploy --force` (first deploy creates the
`registrar-monitor` Worker); verify the live shell, a course page, and a
`/preview/*` image. Existing Telegram posts and shared links point at the old public
site, `https://registrar-monitor.pages.dev`. Keep the Pages project as a
redirect-only deployment instead of deleting it: a directory containing just
`_redirects` with `/* https://registrar-monitor.spooktaken.workers.dev/:splat 301`,
deployed once with `npx wrangler pages deploy <dir> --project-name registrar-monitor`.
`website.base_url` stays at the `workers.dev` URL until the custom domain exists.
Custom domain (planned): buy it in Cloudflare Registrar; add
`"routes": [{ "pattern": "<domain>", "custom_domain": true }]` to
`workers/site/wrangler.jsonc`; set `website.base_url = "https://<domain>"`; deploy;
then redeploy the Pages redirect with `/* https://<domain>/:splat 301`. Do not add
the routes entry before the domain exists.

**Free-tier checks (see "Free tiers" in `docs/operations.md`):** verify the VM machine
type is e2-micro and the boot disk is standard PD, not balanced; review the billing
report for egress and IP SKUs after the first registration period.

**Invariant:** VM SQLite stays the source of truth; the deployed site is a
rebuildable copy. The Worker is a runtime dependency of the dashboard.

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
webhooks through a Tunnel, backups are on R2, the heartbeat is external, and the site
and previews are on the Worker. The VM still generates and deploys the site (Node and
wrangler); moving that to CI is the remaining step to a poller-only VM. The network
watchdog stays: it is generic VM network recovery, not tied to polling.

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
| Worker routing (Stage 7) | Plain `fetch` handler now; **Hono** for the Mini App if it pays off | itty-router, Worktop | With data on Static Assets the Worker has only `/preview/*`, so a plain handler is enough. Mini App `initData` auth and CORS middleware are where Hono (zero dependencies, tests via `app.request()`) would earn its place; itty-router has no middleware ecosystem and Worktop is superseded. |
| Charts (Stage 7, optional) | Keep **Chart.js** | uPlot, ECharts | uPlot is about 50 KB versus about 250 KB, with much lower CPU and memory on long time series, but it lacks built-in annotation and zoom plugins that the dashboard relies on. ECharts is about 1 MB. Switch only if the chart code is being rewritten anyway; prototype the annotation, pinch-zoom, and pinned-readout behavior first. |
| XLS parsing | Keep **xlrd** | python-calamine (Rust) | Calamine is about 1.5x faster in pandas' general benchmarks, but scores lower on `.xls` read fidelity in ExcelBench (2/4 versus 4/4). One 770 KB file per poll does not need the speed; fidelity matters more. Revisit only if parsing shows up in profiles. |

## Considered and rejected

- **htmx + Hono server rendering:** data changes only when a poll runs and
  every visitor sees the same thing, so prebuilt output fits better; the
  Chart.js code would stay, and search would become a network round trip.
- **Postgres or another server database:** each semester's database is 3 MB or
  less.
- **Microservices or an event bus:** the Worker split gives enough separation.
