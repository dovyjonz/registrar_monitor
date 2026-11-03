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
| `registrarmonitor-bot.service` | private subscriptions and digest delivery |
| `registrarmonitor-health.service` | alerts when either unit above leaves `active` |
| `registrarmonitor-network-watchdog.timer` | restarts `systemd-networkd` when metadata connectivity is lost |

`registrar-monitor.service` is retired; never revive it. Cron is not an execution
path. The scheduler is stopped between registration periods, so check live unit
state before assuming anything is running.

`scripts/setup_vps.sh` generates the units but never installs, enables, starts,
stops, or restarts any of them. The scheduler and the bot are separate units and
must stay separate. The health monitor checks both units every 30 seconds and
sends one alert (and one recovery message) to `TELEGRAM_BOT_TEST_USER_ID` using
`TELEGRAM_BOT_TOKEN`; it needs both values in `.env`.

### Files and permissions

The operator owns source, `.git`, `.jj`, and `.venv`. The runtime user owns `data`,
`logs`, generated public output, and `output`. Both accounts are in
`registrarmonitor`; shared directories are setgid with default ACLs. `.env` is
`0600`, owned by the runtime user, with no shared ACL. Do not broaden the checkout
root or `.env` permissions to fix a nested-path problem.

### Change boundaries

Code sharing, VM synchronization, Pages upload, Worker deployment, database
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
paths with temporary write probes, frontend executables, and SQLite integrity,
foreign keys, and schema version. Missing optional state is a warning; integrity
failures and missing required tools are errors. On the VM, run
`scripts/runtime_doctor.sh` as the application user (locked dependencies, no sync).
`monitor db stats` summarizes the databases.

Commands write human-readable stdout and rotating logs in the configured logs
directory. The scheduler also writes one JSON object per decision to
`scheduler_decisions.jsonl`. Expected no-ops are `INFO`, retryable degradation
`WARNING`, failed operations `ERROR`. Logs must not contain secrets, Telegram
identifiers, or full registrar payloads.

## Website publication

The dashboard is a static Cloudflare Pages site generated from matching, non-empty
SQLite snapshots (`monitor deploy`, `--deploy` to upload). `settings.toml` defines
registrar sources, milestones, and publication settings; configured terms without
valid stored courses are not published. Terms order Fall, Spring, Summer within
registrar years.

### Routes and caching

- `/` redirects to the newest publishable semester.
- `/semesters/<semester>/` is the dashboard shell; `/courses/<semester>/<course>/`
  opens a course in it.
- `/data/<semester>/manifest.json` is the revalidating pointer. Versioned manifests,
  blobs, preview JSON and images, and hashed assets are immutable.

Only generated `.html` routes get exact redirects. Removed courses keep their last
route and modal state. Live course share and Open Graph URLs carry the preview token
as `?v=<token>`; closing a course restores the clean semester route. Generated-output
validation rejects private files, root JSON payloads, and obsolete non-index HTML.
Every page is `noindex, nofollow`; `robots.txt` lets crawlers read that directive.

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

### Preview Worker

Preview tokens are the first 48 bits of the preview-visible SHA-256 identity as eight
unpadded URL-safe Base64 characters. A course identity covers only the observations
and events it references, so unrelated changes and no-op polls do not churn its URL;
a semester identity includes the displayed observation time.

`workers/preview-images` validates state-addressed paths, fetches exact preview JSON
from the fixed Pages origin, verifies identity, renders with the Browser binding, and
caches only successful PNGs. It never reads SQLite, contacts the registrar, or
uploads Pages assets. Invalid state is a non-cacheable 404; fetch or render failure
is a non-cacheable 5xx and never blocks publication or substitutes stale content.

Pages upload, VM sync, and restarts do not deploy it. Deploy it separately, with
authorization, whenever its source or configuration changes or the preview token
format, preview schema, or card model changes; otherwise Telegram shows only title
and description.

```bash
make worker-check
npm --prefix workers/preview-images exec wrangler -- deploy \
  --config workers/preview-images/wrangler.jsonc --strict --message "<what changed>"
npm --prefix workers/preview-images exec wrangler -- deployments list \
  --name registrar-monitor-preview-images --json
curl --fail -sSL -o /dev/null -w 'status=%{http_code} type=%{content_type} size=%{size_download}\n' \
  'https://registrar-monitor-preview-images.spooktaken.workers.dev/preview/course/<semester>/<course>/<hash>.png'
```

The probe must return 200, `image/png`, and a nonzero size using an exact `og:image`
URL from a live course page; a 200 course page or preview JSON does not exercise the
Worker. Re-send the course URL afterwards so Telegram refetches the image.

## Telegram

The channel report and the subscription bot share `TELEGRAM_BOT_TOKEN`; the bot never
reads or changes `TELEGRAM_CHAT_ID`. Run it with `monitor bot`. State is at
`directories.telegram_bot_state` in `settings.toml`; enrollment is read directly from
the semester databases.

### Commands and flows

The public menu is `/start` (Add a watch, My watches), `/watch`, `/watches`, `/help`.
Hidden `/import` accepts starred courses copied from the website. The bot works only
in private chats.

- `/watch CSCI 115` adds a whole-course watch; `/watch CSCI 115 / 1L` adds a section;
  other input searches by title. Courses with many sections use paged controls and
  also accept a typed list of section IDs. Review changes shows additions and
  removals before Save applies the scope atomically; Back and Cancel change nothing.
- Stopping a watch, clearing all watches, and deleting bot data require
  confirmation. Data and settings in My watches deletes immediately on confirmation.
- Deep links `t.me/<bot>?start=<payload>` open the bot at a course. They never
  subscribe silently: the payload is resolved against the latest snapshot and needs
  confirmation.
- A new exact watch replies with the state-addressed website link and one large
  top-aligned course preview. Saved confirmations, imports, and digests stay compact.
  If no published link exists, the link and preview button are omitted and the watch
  still works.
- The website's `Copy for bot` / `Copy N for bot` button copies a multi-line `/import`
  command (semester plus starred codes), independent of any bot username. The bot
  validates each course and shows a whole-course receipt before applying. Watches
  belong to one semester and do not carry forward. Each digest links back to My
  watches.

### Runtime

Startup refuses to poll while a webhook or another update consumer is active; the bot
uses direct Bot API polling and never calls `deleteWebhook`. Updates from different
chats run concurrently (limit eight) and stay ordered within one chat. Catalog reads,
delivery rendering, and bot-state writes run off the event loop; catalogs are reused
until the latest snapshot ID changes. Each update logs queue wait, handler duration,
and total latency without identifiers (warning at 3 s or more). Transport failures
log only safe exception types and use the configured retry backoff.

Delivery: flood-control delays are honored, transient failures back off, and a user
who blocks the bot is deactivated until they message it again.

Developer diagnostics: set `TELEGRAM_BOT_TEST_USER_ID` to one numeric user ID and
restart. `/test` appears only in that account's menu and is rejected for everyone
else. It reports the active semester, latest snapshot, uptime, aggregate user and
delivery counts, the operator's watch count, up to five recent identifier-free log
records, and a button that renders one synthetic change through the real digest
formatter without writing state. Unset it and restart to remove the command.

## Backup and recovery

No backup schedule or storage destination is active. Targets for activation: daily
backups of every enrollment database and the bot database, 30 days retention, RPO
24 hours, RTO 4 hours, stored in private encrypted storage outside the VM, with an
alert when the newest successful backup is older than 24 hours. Credentials in
`.env` need a separate protected recovery process. Backups contain private bot state
and never enter Git, CI artifacts, or public output.

Use SQLite's online backup API; copying a live database's main file can omit WAL data.

```bash
uv run python scripts/manage_database.py --semester "Fall 2026" backup \
  output/backups/fall-2026.db
```

It validates `integrity_check` and `foreign_key_check` and never replaces an existing
file, so use a unique dated destination with operator-only permissions. Record time,
source and destination identity, size, SHA-256, and validation results (no
credentials or contents), and delete expired backups only after a newer one is
verified off-host.

Restore rehearsal: download into an isolated directory; verify hash, integrity,
foreign keys, and schema; read the latest snapshot and reporting position per
semester; check the bot store without printing user identifiers; generate the
dashboard locally; record elapsed time. Rehearse before activation and quarterly.
For an incident, with authorization stop affected writers, preserve the damaged
files, restore under the runtime owner, verify, then restart. Bot recovery can replay
deliveries or lose watches created after the backup; reconcile before resuming. Pages
upload stays separately authorized.

## Tooling and CI

- `make check-fast`: Ruff format check, Ruff lint, ty, pytest.
- `make check`: adds frontend lint, typecheck, Node unit tests, and the Vite build.
- `make test-browser`: builds, seeds a deterministic fixture site (isolated, ignored
  SQLite databases; never overwrites an existing database), and runs the Chromium
  smoke tests in `frontend/test/browser-smoke.spec.mjs`. Reports are under
  `output/playwright/`.
- `make worker-check`: preview Worker tests, types, and deployment dry-run.
- `make site-generate`: builds the dashboard and generates the production site.

CI has three jobs: Python 3.13 (Ruff, ty, pytest); Frontend and Worker (lint, unit
tests, build, Worker checks); and Browser smoke, run only when `frontend/` or
`src/registrarmonitor/website/` changes. Coverage is optional:
`uv run pytest --cov=src/registrarmonitor`. Pre-commit runs Ruff and the private-key
check.

Dependabot runs monthly with grouped updates; the `uv` ecosystem keeps `uv.lock`
current. Lockfile changes are verified by CI; syncing the VM is a separate
production action. Pass Playwright options after npm's `--` (for example
`npm --prefix frontend exec -- playwright install --with-deps chromium`). Wrangler is
pinned to the same version in both JavaScript packages; the `sharp` and Worker
`undici` overrides address advisories and can go once upstream Miniflare and the
Worker test pool pin patched versions. The Worker test pool requires Vitest 4, so
Dependabot ignores major Vitest updates.
