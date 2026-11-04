---
status: proposed
updated: 2026-11-03
scope: production rollout, optional VM shrink
---

# Registrar Monitor restructure

Stages 1 through 8 are in the code, including the Telegram Mini App. What is left is
production rollout (below) and the optional Stage 9. This is the only plan: no ADRs
or separate specs. Delete steps as they are done, and the file when all are. It is a
personal project: fewer files, no evidence docs, tests only for behavior users
notice, docs describe the current system, and every production mutation (VM sync,
Worker deploy, database change, service state) needs explicit authorization.

## Remaining production steps

**Cleanup (Stage 1)**

- Archive and delete the unconfigured databases (Decision 1) and `output/`
  (Decision 4). The code rejects any non-v2 database file it opens.
- Run the doctor against every live database after deploying.
- Finalized databases still carry an unused `storage_control` table; drop it or leave
  it.

**VM runtime (Stage 5)**

- Run `scripts/fetch_registrar_ca.sh` on the VM before deploying, so the `ca_bundle`
  exists.
- Create the R2 bucket and Litestream keys, install `litestream` and its unit, and
  test a restore.
- Create a Healthchecks.io check with its Telegram integration and set
  `HEARTBEAT_URL`.
- Set `SENTRY_DSN`.
- Remove the old `registrarmonitor-health.service` unit.
- Free-tier checks (see "Free tiers" in `docs/operations.md`): verify the VM machine
  type is e2-micro and the boot disk is standard PD, not balanced; review the billing
  report for egress and IP SKUs after the first registration period.

**Site Worker (Stage 7)**

- Set `CLOUDFLARE_API_TOKEN` (Workers Scripts edit) and `CLOUDFLARE_ACCOUNT_ID` in the
  VM `.env`; sync the code and run `npm ci --prefix frontend` on the VM; `make
  worker-check`; `monitor deploy --deploy --force` (first deploy creates the
  `registrar-monitor` Worker); verify the live shell, a course page, and a
  `/preview/*` image.
- Existing Telegram posts and shared links point at the old public site,
  `https://registrar-monitor.pages.dev`. Keep the Pages project as a redirect-only
  deployment instead of deleting it: a directory containing just `_redirects` with
  `/* https://seatwatch.fyi/:splat 301`, deployed once with
  `npx wrangler pages deploy <dir> --project-name registrar-monitor`.

**Telegram (Stage 6 and the Mini App)**

- Create the `cloudflared` tunnel and config for `bot.seatwatch.fyi`, install the
  tunnel unit, and set `TELEGRAM_WEBHOOK_URL=https://bot.seatwatch.fyi/telegram` and
  `TELEGRAM_WEBHOOK_SECRET`; deploy the bot unit after it.
- Then `monitor deploy --deploy --force` so the pages and headers allow Telegram's
  script and the API. Open the bot's menu button to check the Mini App.
- Optional: queued delivery (a Cloudflare Queue consumer honoring `RetryAfter`) only
  if digests grow.

**Analysis (Stage 8)**

- Upload the Parquet exports to R2 next to the dashboard.
- Optional: an "Explore data" page using DuckDB-WASM over the exports.
- Optional: move website read-model builders to DuckDB only where clearly simpler or
  faster.

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
