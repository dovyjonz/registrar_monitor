# Operations

Commands: `make help` and `monitor --help`. Times use `Asia/Almaty`. Never print
`.env`, tokens, chat IDs, process environments, or unrestricted journals.

## Production

| Item | Value |
|---|---|
| Google Cloud project / VM / zone | `registrarmonitor` / `instance-20260501-152532` / `us-east1-c` |
| OS | Debian 12 |
| Project root | `/home/dmitry_s_ivanenko/registrar_monitor` |
| Runtime user / SSH operator / shared group | `dmitry_s_ivanenko` / `spook` / `registrarmonitor` |

| Unit | Purpose |
|---|---|
| `registrarmonitor.service` | scheduler, reports, dashboard publication |
| `registrarmonitor-bot.service` | private subscriptions, digest delivery, and the Mini App API |
| `registrarmonitor-tunnel.service` | Cloudflare Tunnel (`cloudflared`) forwarding the bot hostname (Telegram webhook and Mini App API) to the bot's loopback listener |
| `registrarmonitor-litestream.service` | continuous replication of every SQLite database to R2 |
| `registrarmonitor-network-watchdog.timer` | restarts `systemd-networkd` when metadata connectivity is lost |

`registrar-monitor.service` is retired; never revive it. Cron is not an execution
path. The scheduler is stopped between registration periods, so check live unit
state before assuming anything is running.

`scripts/setup_vps.sh` generates the units but never installs, enables, starts,
stops, or restarts any of them. The scheduler and the bot are separate units and
must stay separate. The tunnel needs `scripts/cloudflared-config.yml` (untracked,
from `scripts/cloudflared-config.yml.template`) and the Litestream unit needs the
`litestream` binary plus the R2 variables in `.env`.

Run it as the SSH operator, naming the runtime account, so the units run as that
user and not as the operator:

```bash
RUNTIME_USER=dmitry_s_ivanenko scripts/setup_vps.sh
```

The scheduler and bot units run `.venv/bin/monitor` directly (no `uv run`, so no
start-up cost and no chance of a sync). The operator owns `.venv` and syncs it; the
runtime user cannot write to it.

Resource limits (override via `SCHEDULER_MEMORY_*` / `BOT_MEMORY_*` when running the
script): the scheduler gets `MemoryHigh=300M`, `MemoryMax=400M`, and
`OOMScoreAdjust=-500` so it is the last process the kernel kills; the bot gets
`MemoryHigh=200M`, `MemoryMax=300M`, and `OOMScoreAdjust=-200`. The scheduler unit
also sets `XDG_RUNTIME_DIR=/run/user/<runtime uid>`, because the poller runs the
website build in its own `systemd-run --user --scope` with a separate memory cap and
low CPU and I/O priority. That needs the runtime user's systemd manager running with
no login session, so setup runs `sudo loginctl enable-linger <runtime user>` (it warns
with the command if sudo is not available).

After re-running setup and installing the units, verify on the VM:

```bash
loginctl show-user <runtime user> --property=Linger          # Linger=yes
cat /sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/cgroup.controllers
                                                             # must list "memory"
sudo -u <runtime user> XDG_RUNTIME_DIR=/run/user/<uid> \
  systemd-run --user --scope -p MemoryMax=64M true           # exits 0
systemctl show registrarmonitor.service -p MemoryMax -p OOMScoreAdjust
```

Memory controller delegation to `user@.service` is the default on modern systemd; if
`memory` is missing, `MemoryMax` on the build scope is silently not enforced.
`scripts/tune_vm.sh` installs the drop-in that fixes it (see "VM tuning").

### Deploy

Sync and restart are separate steps. `make deploy-vm REV=<revision>` runs
`scripts/deploy_vm.sh sync`: fetch and check out the revision on the VM, `uv sync
--locked`, and `npm ci --prefix frontend` only when the frontend lockfile changed. It
restarts nothing, so running processes keep the old code. Add `UNIT=<unit>.service`
to also run `scripts/deploy_vm.sh restart`, which restarts that one unit only if it
is currently active, then waits `HEALTH_WAIT` seconds (default 30) and fails if the
unit leaves `active` or restarts on its own. A stopped unit is never started; in
particular the scheduler stays stopped between registration periods. The bot has no
health endpoint, so its check is the stable `active` state. Deploy well outside a
milestone window and only when explicitly authorized.

Configuration comes from the environment: `VM_HOST`, `VM_PATH`, optional `VM_USER`
and `VM_REMOTE`. Plain `ssh` is the default. `gcloud compute ssh` uses its own key
and known-hosts, so with it set `VM_SSH=gcloud`, `VM_HOST=<instance name>`, and
`GCLOUD_SSH_ARGS="--project registrarmonitor --zone us-east1-c"`. Pushing the
revision first is required.

Installs always run here as the SSH operator, which owns `frontend/node_modules`
(group-readable for the runtime user). Do not run `npm install` or `npm ci` as the
runtime user.

### Period start and stop

End of a period: stop `registrarmonitor.service` (authorized), then run
`scripts/healthcheck_pause.sh` on the VM so the Healthchecks.io check does not alert
while polling is deliberately off. It calls the management API
(`POST /api/v3/checks/<uuid>/pause`) with `HEALTHCHECKS_API_KEY` and
`HEALTHCHECKS_CHECK_UUID` from `.env` and prints no secrets. Start of a period:
sync and verify first (`monitor doctor`), then start the scheduler. The check
resumes by itself on the first successful poll's ping; there is no separate resume
step. Create the read-write API key in the Healthchecks.io project settings.

After a long stop, before the first start:

1. `monitor rehearse [--semester S]` copies the semester database to a scratch
   directory (all `DIRECTORIES__*` paths redirected), clears the Telegram, Cloudflare,
   heartbeat, and Sentry credentials, runs one download, store, and report cycle on the
   copy, and checks that the live database files are unchanged. It prints whether the
   download and parse worked, the parsed size, whether a snapshot was stored, and the
   catch-up report size. `--keep` leaves the scratch directory for inspection. It
   sends, queues, and deploys nothing.
2. `monitor poll` (storage must already exist via `monitor db initialize --semester S`;
   polling never creates it) stores the current state, then `monitor baseline [--semester S]`
   records the latest snapshot as reported (it prints the old and new snapshot ids).
   Otherwise the first start would post everything that changed since the last
   report to the channel and queue subscriber digests for it. Baseline sends nothing
   and is safe to repeat. Both change the live database, so they need explicit
   authorization like any other production write.

### Registrar TLS

Downloads always verify the registrar's certificate. `data_source.ca_bundle` in
`settings.toml` names a PEM file with the intermediate and root certificates; create
it on the polling host with `scripts/fetch_registrar_ca.sh` (defaults to
`data/registrar-chain.pem`) and re-run it if the registrar changes its chain.
`scripts/fetch_registrar_ca.sh` exits non-zero if the connection fails, no
certificate is sent, or the written chain does not verify. `monitor doctor` warns when
any certificate in the bundle expires within 30 days; refresh it then. Without a
bundle, system verification applies. The registrar file embeds a generation
timestamp, so its bytes differ on every download; the scheduler instead compares a
digest of the parsed rows with the previous poll's, and an unchanged poll skips the
snapshot write, comparison, report, and publish. `data/downloads` keeps the newest 5
files and `data/changes` the newest 200 reports. Each poll logs one line,
`Poll <status> duration=… peak_rss=…`; each generation phase logs its duration and
peak RSS, and each digest batch logs `Digest batch processed … peak_rss_mb=…`.

### Files and permissions

The operator owns source, `.git`, `.jj`, and `.venv`. The runtime user owns `data`,
`logs`, generated public output, and `output`. Both accounts are in
`registrarmonitor`; shared directories are setgid with default ACLs. `.env` is
`0600`, owned by the runtime user, with no shared ACL. Do not broaden the checkout
root or `.env` permissions to fix a nested-path problem.

### VM tuning

The VM has 1 GB of RAM, 2 GB of disk swap, a small slow persistent disk, and a
shared-core CPU. Goal: the poller and bot are never starved by a website build or a
registration burst, and swap and writeback do not stall them. `scripts/setup_vps.sh`
tunes the project units; `scripts/tune_vm.sh` tunes the operating system.

**Unit budget** (set by `setup_vps.sh`; override with the `*_MEMORY_*` variables).
"Typical" is the scheduler's measured peak and the build's measured share of RAM;
the other rows are estimates until measured with `tune_vm.sh --status` during a period.

| Unit | MemoryHigh / MemoryMax | OOMScoreAdjust | CPU / IO weight | Typical |
|---|---|---|---|---|
| `registrarmonitor` (scheduler) | 300M / 400M | -500 | 200 / 200 | 68M |
| `registrarmonitor-bot` | 200M / 300M | -200 | 150 / 150 | ~90M |
| `registrarmonitor-tunnel` | 96M / 128M | 0 | 75 / 75 | ~40M |
| `registrarmonitor-litestream` | 128M / 192M | 200 | 50 / 50, `Nice=10` | ~60M |
| website build scope (transient) | none / `website.build_memory_max` (600M) | 500 | 20 / 20, nice 10, ionice idle | ~220M (23%) |
| kernel, systemd, journald, sshd, guest agents | uncapped | 0 | 100 / 100 | ~150M |

The caps are ceilings, not a forecast: they sum to more than RAM (services 1,020M,
plus 600M for the build). Expected use is about 410M steady and about 630M during a
build, which leaves the rest for page cache. If every cap were hit, zram and the disk
swap absorb the excess, and the kernel kills in this order: build, Litestream, other
processes, bot, scheduler. The scheduler and bot also run with `MALLOC_ARENA_MAX=2`
(glibc per-thread arenas inflate RSS), the scheduler with
`NODE_OPTIONS=--max-old-space-size=384` so vite and wrangler children collect garbage
inside the build cap, and the Go units with a `GOMEMLIMIT` below their `MemoryHigh`.
`IOWeight`, `ionice -c 3`, and the build's I/O priority only take effect under the
`bfq` I/O scheduler (see `--bfq` below); with the default `mq-deadline` or `none` they
are inert, while CPU weights and memory caps work regardless.

**What `scripts/tune_vm.sh` does.** Dry run by default; it prints the current values
and the plan, and changes nothing without `--apply`. It uses `sudo`, is idempotent
(files with matching content are left alone), never restarts a project unit, never
touches `/etc/fstab`, networking, or `google-guest-agent`, and never reads `.env`.

| Step | Change |
|---|---|
| `zram` | installs `zram-tools`, sets zstd at 50% of RAM, priority 100 (disk swap, priority -2, stays as the fallback), enables `zramswap.service`. Skipped when the kernel has no zram. If zram already holds more than 64 MiB, a settings change waits for a reboot. |
| `sysctl` | `vm.dirty_background_bytes` 16 MiB, `vm.dirty_bytes` 64 MiB (bound writeback stalls on the slow disk), `vm.vfs_cache_pressure` 50, and, only with zram, `vm.swappiness` 100 and `vm.page-cluster` 0 |
| `thp` | transparent hugepages to `madvise` (Debian defaults to `always`, which bloats small processes) |
| `journald` | persistent, compressed, `SystemMaxUse=200M`, 14 days, rate limit 5000 per 30 s; restarts `systemd-journald` |
| `timers` | `apt-daily` at 03:30 and `apt-daily-upgrade` at 04:00 `Asia/Almaty` (20 min random delay), `fstrim` Mondays 04:40, all `Persistent=false` so a boot or timer restart never triggers a catch-up run; disables `man-db.timer`. Check the next milestone in `settings.toml` first: 03:30-04:40 must not fall in a registration window. |
| `delegation` | drop-in so `user@.service` delegates `memory pids cpu io`, which the build scope's `MemoryMax`, `CPUWeight`, and `IOWeight` need. It is read when `user@<uid>.service` next starts (reboot, or restart it while no build runs). |

Opt-in only: `--disable-osconfig` stops `google-osconfig-agent` (saves tens of MB;
GCP OS Config patching and inventory stop), and `--bfq` switches the disk to the `bfq`
scheduler (makes the build's idle I/O class and `IOWeight` effective; costs some CPU).
`--skip zram,timers,...` leaves steps out.

Run on the VM as the SSH operator, naming the runtime account:

```bash
RUNTIME_USER=dmitry_s_ivanenko scripts/tune_vm.sh            # dry run
RUNTIME_USER=dmitry_s_ivanenko scripts/tune_vm.sh --apply    # only when authorized
```

Apply well outside a milestone window; installing zram and restarting journald are
brief but real activity on a 1 GB VM. Also re-run `scripts/setup_vps.sh`, then install
the regenerated units and restart them one at a time (each is a separate authorized
action) so the new limits and environment take effect.

**Verify.** `scripts/runtime_doctor.sh --vm` (or `scripts/tune_vm.sh --status`) is
read-only and needs no sudo. It reports swap devices and zram usage, the sysctls,
THP and I/O scheduler, whether `user@<uid>.service` delegates `memory`, journald disk
usage, the maintenance timers, and each project unit's current memory and limits.
Also `swapon --show` (zram first, priority 100), `systemctl list-timers 'apt-daily*'`,
and the `systemd-run` check under "Production".

**Revert.** Each item is a file or state this script created; remove it and reload:

| Created | Undo |
|---|---|
| `/etc/default/zramswap` (original at `.registrarmonitor.bak`), `zramswap.service` | restore the backup, or `sudo apt-get remove zram-tools`; reboot or `swapoff /dev/zram0` |
| `/etc/sysctl.d/90-registrarmonitor.conf` | delete, then `sudo sysctl --system` (the old values return after reboot) |
| `/etc/tmpfiles.d/90-registrarmonitor-thp.conf` | delete; `echo always` to the sysfs knob or reboot |
| `/etc/systemd/journald.conf.d/90-registrarmonitor.conf` | delete, `sudo systemctl restart systemd-journald` |
| `/etc/systemd/system/{apt-daily,apt-daily-upgrade,fstrim}.timer.d/90-registrarmonitor.conf` | delete the files, `sudo systemctl daemon-reload`, restart the timers; `sudo systemctl enable --now man-db.timer` |
| `/etc/systemd/system/user@.service.d/90-registrarmonitor-delegate.conf` | delete, `sudo systemctl daemon-reload` |
| `/etc/udev/rules.d/60-registrarmonitor-io-scheduler.rules` (`--bfq`) | delete, `sudo udevadm control --reload`, reboot |
| `google-osconfig-agent` (`--disable-osconfig`) | `sudo systemctl enable --now google-osconfig-agent` |

Not done on purpose: no fstab or mount-option changes (`noatime`, `commit=`), no
`MemorySwapMax` on the scheduler (forbidding swap makes it the first thing killed under
pressure), no `vm.min_free_kbytes` or overcommit changes, and no change to the page
cache or database settings; there is no measurement yet that justifies them.

### Change boundaries

Code sharing, VM synchronization, Worker deployment (which publishes the site), database
mutation, and service state are separate actions; each production mutation needs
explicit operator authorization. A code sync does not reload a running process. After
an authorized restart, verify unit state, PID and start time, and source checksums.
A unit-file copy or `daemon-reload` does not authorize enablement or startup.

### Safe inspection

Validate the exact leaf command with `--help`, specify project and zone, and preview
SSH with `--dry-run` first. Keep journal queries to one named unit and a bounded time
or line count.

```bash
gcloud compute ssh instance-20260501-152532 --project registrarmonitor \
  --zone us-east1-c --quiet --dry-run \
  --command="sudo -n systemctl show registrarmonitor.service --property=ActiveState --property=SubState --property=UnitFileState --property=MainPID --no-pager"
```

## Doctor and logs

`monitor doctor` (`--json`, `--output`) checks toolchains and lockfiles, required
paths with temporary write probes, frontend executables, the `ca_bundle` expiry, and SQLite integrity,
foreign keys, and schema version. Missing optional state is a warning; integrity
failures and missing required tools are errors. On the VM, run
`scripts/runtime_doctor.sh` as the application user (it runs the same `.venv/bin/monitor` as the units).
`monitor db stats` summarizes the databases.

Commands log one plain-text line per event (`time LEVEL logger: message`) to stdout
and to a single rotating `registrar_monitor.log` in the configured logs directory;
the level is colored only on a TTY, and `print` is kept for command results such as
tables and JSON. `doctor` emits no log lines. The `schedule` and `bot` services (or
`LOG_FORMAT=json`) log JSON lines to the journal only, with no log file; filter them
with `journalctl -u registrarmonitor.service -o cat | jq -R 'fromjson? | select(.level != "INFO")'`.
The scheduler also writes one JSON object per decision to
`scheduler_decisions.jsonl`. Expected no-ops are `INFO`, retryable degradation
`WARNING`, failed operations `ERROR`. Logs must not contain secrets, Telegram
identifiers, or full registrar payloads.

### Heartbeat and errors

After every successful poll, changed or unchanged, the scheduler pings
`HEARTBEAT_URL` (a Healthchecks.io check). Configure the check's grace period and its
Telegram integration there; a quiet scheduler alerts without a unit on the VM. A
failed ping is logged (never the URL) and does not affect polling. Set `SENTRY_DSN`
(and optionally `SENTRY_ENVIRONMENT`) to report unhandled errors to Sentry; unset, it
is off.

The network watchdog timer is generic VM network recovery and is independent of
polling.

## Website publication

The dashboard is a static site generated from matching, non-empty SQLite snapshots
(`monitor deploy`; `--deploy` runs `wrangler deploy`, `--dry-run` runs it with
`--dry-run` after generating, `--upload-only` deploys the existing output) and served as the Static Assets
of the `registrar-monitor` Worker (`workers/site`). `settings.toml` defines
registrar sources, milestones, and publication settings; configured terms without
valid stored courses are not published. Terms order Fall, Spring, Summer within
registrar years.

### Routes and caching

- `/` redirects to the newest publishable semester.
- `/semesters/<semester>/` is the dashboard shell; `/courses/<semester>/<course>/`
  opens a course in it. Every course route is its own generated page, because each
  carries its own Open Graph tags.
- `/data/<semester>/manifest.json` is the revalidating pointer. Versioned manifests,
  blobs, preview JSON and images, and hashed assets are immutable.
- `_headers` (security headers and the cache rules above) and `_redirects` are
  generated into `frontend/public` and applied by Static Assets.
- Static Assets requests are free, unlimited, and never invoke the Worker. Only
  `/preview/*` does (`assets.run_worker_first`), which keeps the site far under the
  free plan's 100,000 daily Worker invocations. Keep the file count under
  the free plan's 20,000 assets per version (about 1,300 now).
- Cloudflare Web Analytics: set `website.web_analytics_token` in `settings.toml`
  (a 32-character hex site token); the cookieless beacon is added to generated pages
  only when it is non-empty.

Only the old semester `.html` pages get exact redirects; old course `.html` URLs are
not redirected (one per course exceeded the 2,000-redirect limit). Unchanged pages and
data files are not rewritten. Removed courses keep their last
route and modal state. Live course share and Open Graph URLs carry the preview token
as `?v=<token>`; closing a course restores the clean semester route. Generated-output
validation rejects private files, root JSON payloads, and obsolete non-index HTML.
Every page is `noindex, nofollow`; `robots.txt` lets crawlers read that directive.
`.checksums.json` and `.frontend-build-hash` stay local (`.assetsignore`).

The frontend bundle is rebuilt only when `frontend/src`, `package.json`,
`package-lock.json`, or `vite.config.mjs` change (hash in `.frontend-build-hash`), so
routine publications do not run Node builds on the 1 GB VM. Frontend builds rely
on the VM's 2 GB of swap. Wrangler uploads only
assets Cloudflare does not already have, so each deploy sends just the changed pages
and data.

### Enrollment semantics

Availability uses active sections: per section type, sum `max(capacity -
enrollment, 0)`; a multi-type course's registration places are the minimum type
total (tied types all limit). Removed sections do not count; over-capacity sections
contribute zero. When a required type is full, `required-type-full` outranks
Open/Near/Full (compact copy `LAB FULL`, roomier copy names the type); single-type
courses stay `FULL`.

Archived publication uses the last real observation inside the configured milestone
window for enrollment, capacity, status, chart endpoint, and preview. Later polls are
not appended. Cards show `FULL` when exactly full and the rounded percentage when
average enrollment exceeds capacity. Charts normalize the limiting section type's
enrollment and capacity against its active sections' opening capacities; a later
section contributes its own initial capacity from when it becomes active.

### Site Worker

Preview tokens are the first 48 bits of the preview-visible SHA-256 identity as eight
unpadded URL-safe Base64 characters. A course identity covers only the observations
and events it references, so unrelated changes and no-op polls do not churn its URL;
a semester identity includes the displayed observation time.

`workers/site` validates state-addressed `/preview/*` paths, reads the exact preview
JSON from its own Static Assets, verifies identity, renders with the Browser binding,
and caches only successful PNGs. It never reads SQLite or contacts the registrar.
Invalid state is a non-cacheable 404; read or render failure is a non-cacheable 5xx
and never blocks publication or substitutes stale content. The Telegram Mini App API is
not on this Worker; the bot serves it (see Telegram).

`monitor deploy --deploy` (and the scheduler, as `monitor deploy --upload-only` in a
capped child process) runs `wrangler deploy` for the Worker
and the site together; it needs `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` in `.env`. The token needs Account > Workers Scripts > Edit,
Zone > Workers Routes > Edit on `seatwatch.fyi`, and Zone > Zone > Read. The
`registrar-monitor` Worker already exists. On the VM only the frontend dependencies
are installed (`npm ci --prefix frontend`). Deploy by hand, with authorization, whenever the
Worker source or configuration changes or the preview token format, preview schema,
or card model changes; otherwise Telegram shows only title and description.

Run `make worker-check` on a workstation or in CI, not on the VM. Then:

```bash
npm --prefix frontend exec wrangler -- deploy \
  --config workers/site/wrangler.jsonc --strict --message "<what changed>"
npm --prefix frontend exec wrangler -- deployments list --name registrar-monitor --json
curl --fail -sSL -o /dev/null -w 'status=%{http_code} type=%{content_type} size=%{size_download}\n' \
  'https://seatwatch.fyi/preview/course/<semester>/<course>/<hash>.png'
```

The probe must return 200, `image/png`, and a nonzero size using an exact `og:image`
URL from a live course page; a 200 course page or preview JSON does not exercise the
Worker. Re-send the course URL afterwards so Telegram refetches the image. Roll back
with `wrangler rollback`.

The site's public origin is `https://seatwatch.fyi`, a custom-domain route in
`workers/site/wrangler.jsonc` that must match `website.base_url`. The domain is
registered and its zone hosted on Cloudflare. Old links point at
`https://registrar-monitor.pages.dev`. Keep that Pages project as a redirect-only
deployment (a directory with just `_redirects` containing
`/* https://seatwatch.fyi/:splat 301`, deployed once with
`npx wrangler pages deploy <dir> --project-name registrar-monitor`) rather than
deleting it.

## Telegram

The channel report and the subscription bot share `TELEGRAM_BOT_TOKEN`; the bot never
reads or changes `TELEGRAM_CHAT_ID`. Run it with `monitor bot`. State is at
`directories.telegram_bot_state` in `settings.toml`; enrollment is read directly from
the semester databases.

### Commands and flows

The public menu is `/start` (Add a watch, My watches), `/watch`, `/watches`, `/help`.
Hidden `/import` accepts starred courses copied from the website. The bot works only
in private chats. Browsing, searching, and picking sections happen in the Mini App
(below); the bot keeps only the exact-code and confirmation flows.

- `/watch CSCI 115` adds a whole-course watch; `/watch CSCI 115 / 1L` adds a section.
  Plain messages and comma- or newline-separated lists of exact codes work the same
  way. Anything that is not an exact code gets the prompt back; there is no title
  search or paged catalog in the bot.
- Stopping a watch, clearing all watches, and deleting bot data require
  confirmation. Data and settings in My watches deletes immediately on confirmation.
- Deep links `t.me/<bot>?start=<payload>` open the bot at a course. Each course
  heading in a channel report links to `?start=watch_<semester>_<course>`. They never
  subscribe silently: the payload is resolved against the latest snapshot (stale,
  unknown, or ambiguous codes are rejected) and needs confirmation.
- A new exact watch replies with the state-addressed website link and one large
  top-aligned course preview. Saved confirmations, imports, and digests stay compact.
  If no published link exists, the link and preview button are omitted and the watch
  still works.
- The website's `Copy for bot` / `Copy N for bot` button copies a multi-line `/import`
  command (semester plus starred codes), independent of any bot username. The bot
  validates each course and shows a whole-course receipt before applying. Watches
  belong to one semester and do not carry forward. Each digest links back to My
  watches.

### Mini App

The dashboard doubles as a Telegram Mini App. Opened from Telegram, the course modal
shows Watch/Unwatch buttons for the course or the selected section. The bot process
serves the API itself, so no Worker sits in the path:

- `GET /miniapp/watches`, `POST /miniapp/watches` (body: `semester`, `course`,
  optional `section`), and `DELETE /miniapp/watches/<id>`, on the same listener and
  Tunnel hostname as the webhook (for example `https://bot.seatwatch.fyi/miniapp`).
- Each request carries `Authorization: tma <initData>`. The bot verifies Telegram's
  HMAC with the bot token, rejects `auth_date` older than 24 hours, and takes the user
  from the signed data; it never accepts a client-supplied user id. Adding validates
  against the latest snapshot and goes through the same store call as `/watch`, so
  dedupe is identical. Only the active semester can be watched.
- CORS allows only the `website.base_url` origin; any other `Origin` gets 403.
- At startup the bot sets the chat menu button to open the dashboard (the active
  semester page, because the site root's redirect drops Telegram's launch
  parameters). After a semester rollover, publish the site first, then restart the
  bot to move it. With the Mini
  App disabled, the menu button is reset to Telegram's default.
- The dashboard loads `telegram.org/js/telegram-web-app.js` only when the URL carries
  Telegram's launch parameters, so ordinary visitors load nothing extra. No BotFather
  step is needed beyond what the bot sets itself; `/setdomain` is for the Login
  Widget and is not required for Web Apps.

Enable it with `website.miniapp_api_url = "https://bot.seatwatch.fyi/miniapp"` in
`settings.toml`, run `monitor deploy --deploy --force` so the generated pages and
`_headers` allow Telegram's script, the API origin, and framing by Telegram Web, and
only then restart the bot, because it points its menu button at the site at startup. Empty turns everything off. The named Tunnel hostname needs
the custom domain's zone on Cloudflare, so the webhook and the Mini App go live
after the domain exists.

### Runtime

The bot runs one ASGI server (Starlette under uvicorn) inside its process, behind a
Cloudflare Tunnel: it listens on `telegram_bot.webhook_listen`:`webhook_port`
(loopback by default), registers `TELEGRAM_WEBHOOK_URL` with Telegram at startup, and
rejects any `POST` to the webhook path whose `X-Telegram-Bot-Api-Secret-Token` is not
`TELEGRAM_WEBHOOK_SECRET` (403). Startup fails without a valid secret and an `https`
URL. The VM opens no inbound port. Updates from different
chats run concurrently (limit eight) and stay ordered within one chat. Catalog reads,
delivery rendering, and bot-state writes run off the event loop; catalogs are reused
until the latest snapshot ID changes. Each update logs queue wait, handler duration,
and total latency without identifiers (warning at 3 s or more). Transport failures
log only safe exception types and use the configured retry backoff. Access logs are
off.

Delivery: flood-control delays are honored, transient failures back off, and a user
who blocks the bot is deactivated until they message it again.

Developer diagnostics: set `TELEGRAM_BOT_TEST_USER_ID` to one numeric user ID and
restart. `/test` appears only in that account's menu and is rejected for everyone
else. It reports the active semester, latest snapshot, uptime, aggregate user and
delivery counts, the operator's watch count, up to five recent identifier-free log
records, and a button that renders one synthetic change through the real digest
formatter without writing state. Unset it and restart to remove the command.

## Backup and recovery

Litestream replicates every `data/enrollment_*.db` and `data/telegram_bot.db` to a
private R2 bucket (`scripts/litestream.yml`; bucket, account, and keys come from
`.env`, see `.env.example`) with 30-day retention, daily snapshots, and a 5-minute sync interval, which gives
point-in-time restore. Backups contain private bot state and never enter Git, CI
artifacts, or public output. Alert on a stale replica from R2 or Litestream logs.

Restore to a new file, never over a live database:

```bash
scripts/restore_litestream.sh data/enrollment_fall_2026.db /tmp/restored.db
scripts/restore_litestream.sh data/telegram_bot.db /tmp/bot.db 2026-11-01T12:00:00Z
```

Rehearse into an isolated directory before relying on it and quarterly: verify
`monitor doctor` integrity, foreign keys, and schema, read the latest snapshot and
reporting position per semester, check the bot store without printing user
identifiers, and generate the dashboard locally. For an incident, with authorization
stop affected writers, preserve the damaged files, restore under the runtime owner,
verify, then restart. Bot recovery can replay deliveries or lose watches created
after the last replicated write; reconcile before resuming. Site Worker deployment
stays separately authorized.

## Analysis

`monitor analyze` lists the saved DuckDB queries in `analysis/*.sql`;
`monitor analyze <name> [--csv FILE]` runs one across every configured semester
database, attached read-only through DuckDB's `sqlite` extension (downloaded on first
use). It never writes to SQLite.

## Free tiers

| Service | Free limit | How the design stays within it |
|---|---|---|
| Cloudflare Workers | 100k Worker invocations/day | Only `/preview/*` invokes the Worker; static asset requests are free and unlimited. The Mini App API runs on the VM, not the Worker |
| Workers Static Assets | 20,000 assets per version, 25 MiB per file | About 1,300 files now; keep the count well under the cap |
| Cache API | Does nothing on `workers.dev` | `workers_dev` and preview URLs are off; on the custom domain `/preview/*` responses are cached with `caches.default` |
| Cloudflare R2 | 10 GB-month, 1M Class A, 10M Class B per month | Litestream only; a 5-minute sync interval is about 8.6k PUTs/month per active database |
| GCP Always Free | One e2-micro in us-west1, us-central1, or us-east1 | The VM is in us-east1-c |
| GCP disk | 30 GB standard persistent disk (not balanced, the default for new VMs) | Boot disk must be standard PD |
| GCP egress | 1 GB/month from North America | Tightest limit: Litestream, `wrangler deploy` (uploads only changed assets), Telegram, and Sentry all count |
| Healthchecks.io | 20 checks | One heartbeat check |
| Sentry | Free tier event quota | Unhandled errors only |
| Custom domain | Not free | The one paid item, about $10/year (Cloudflare Registrar) |

## Tooling and CI

- `make check-fast`: Ruff format check, Ruff lint, ty, pytest.
- `make check`: adds frontend lint, typecheck, Node unit tests, and the Vite build.
- `make test-browser`: builds, seeds a deterministic fixture site (isolated, ignored
  SQLite databases; never overwrites an existing database), and runs the Chromium
  smoke tests in `frontend/test/browser-smoke.spec.mjs`. Reports are under
  `output/playwright/`.
- `make worker-check`: site Worker tests, types, and `wrangler deploy --dry-run`.
- `make site-generate`: builds the dashboard and generates the production site.
- `make deploy-vm`: authorized VM sync of one revision, optionally restarting one active unit (see Deploy).

CI has three jobs: Python 3.13 (Ruff, ty, pytest); Frontend and Worker (lint, unit
tests, build, Worker checks); and Browser smoke, run only when `frontend/` or
`src/registrarmonitor/website/` changes. Coverage is optional:
`uv run pytest --cov=src/registrarmonitor`. Pre-commit runs Ruff and the private-key
check.

Dependabot runs monthly with grouped updates; the `uv` ecosystem keeps `uv.lock`
current. Lockfile changes are verified by CI; syncing the VM is a separate
production action. Pass Playwright options after npm's `--` (for example
`npm --prefix frontend exec -- playwright install --with-deps chromium`). Wrangler is
pinned to the same version in both JavaScript packages (the frontend copy deploys
from the VM); the `sharp` and Worker
`undici` overrides address advisories and can go once upstream Miniflare and the
Worker test pool pin patched versions. The Worker test pool requires Vitest 4, so
Dependabot ignores major Vitest updates.
