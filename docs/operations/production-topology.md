# Production topology

This document records a read-only inspection of the Registrar Monitor production
VPS performed on **2026-07-28**. Runtime timestamps below use the VPS local time
unless stated otherwise. The evidence combines operator-authorized command output
from the VPS with the repository documentation and source at the time of review.

Classification meanings:

- **Verified** — directly established by production command output or repository
  source for the behavior being described.
- **Inferred** — strongly supported by the combined evidence, but not directly
  observed in the production runtime.
- **Unknown** — not established by the available evidence.

## Executive status

- **Verified:** No Registrar Monitor polling, reporting, deployment, or scheduler
  process was running when the VPS was inspected.
- **Verified:** The cron daemon was active, but there was no active Registrar
  Monitor job in the inspected user, service-user, root, or system crontabs.
- **Verified:** Neither of the two installed Registrar Monitor systemd units was
  active. `registrarmonitor.service` was disabled and failed;
  `registrar-monitor.service` was enabled and failed.
- **Verified:** Production polling, reporting, and website deployment have been
  stale since 2026-06-10.
- **Inferred:** The intended production topology is one long-running systemd
  scheduler, not concurrent systemd and cron execution. Which of the two installed
  units is intended to be authoritative is unknown.

## Runtime host and paths

| Item | Classification | Production value or finding |
|---|---|---|
| Host platform | **Verified** | Google Compute Engine VM running Debian 12 |
| Authoritative timezone | **Verified** | `Asia/Almaty` (`UTC+05`) |
| Clock state | **Verified** | System clock synchronized; NTP active; RTC maintained in UTC |
| Project root | **Verified** | `/home/dmitry_s_ivanenko/registrar_monitor` |
| Database directory | **Verified** | `/home/dmitry_s_ivanenko/registrar_monitor/data` |
| Runtime environment file | **Verified** | `/home/dmitry_s_ivanenko/registrar_monitor/.env` |
| Current deployed revision | **Verified** | Git revision beginning `668893f`, committed 2026-06-01 |
| Relationship to this repository | **Verified** | The VPS revision predates the repository revision used for this report |

- **Verified:** `settings.toml` resolves relative directory settings from the
  project root. Its production `data_storage = "data"` setting therefore resolves
  to the database directory shown above.
- **Verified:** Enrollment history is stored in one SQLite database per semester
  under the database directory, with a legacy/default `enrollment.db` also present.
- **Verified:** The runtime `.env` file exists with restrictive file permissions.
  Its contents were not printed or copied during this review.

## Polling and process supervision

### Installed units

| Unit | Classification | Enabled state | Runtime state | Command | Restart policy |
|---|---|---:|---|---|---|
| `registrarmonitor.service` | **Verified** | Disabled | Failed since 2026-06-10 16:42:56 +05 | `uv run monitor schedule` | Always; 5-second delay |
| `registrar-monitor.service` | **Verified** | Enabled | Failed since 2026-05-26 14:52:29 +05 | `uv run monitor schedule` | Always; 10-second delay |

- **Verified:** Both units specify a start limit of five starts within ten seconds.
- **Verified:** Both units last recorded exit status 143 after systemd stop
  operations. Neither subsequently restored an active scheduler.
- **Verified:** The enabled hyphenated unit had no recorded automatic restarts
  after its last stop (`NRestarts=0`).
- **Inferred:** `Restart=always` did not restore either unit because systemd treats
  an operator-initiated stop differently from an unexpected process failure.
- **Verified:** Host-level recovery is currently ineffective because no Registrar
  Monitor service is active.

### Scheduler behavior

- **Verified:** `monitor schedule` performs polling and can trigger background
  report generation and website deployment after significant changes, subject to
  scheduler cooldowns.
- **Verified:** The scheduler catches and logs many poll, report, and deployment
  exceptions so that an individual workflow failure does not necessarily terminate
  the scheduler loop.
- **Inferred:** A failure escaping the scheduler process should normally cause
  systemd to retry according to the installed unit's restart and start-limit
  settings.
- **Unknown:** There is no evidence of an external health check or alert that
  notices a stopped scheduler or stale poll/report/deployment timestamps.

## systemd and cron responsibilities

- **Verified:** The cron daemon was active at inspection time.
- **Verified:** The service user's only Registrar Monitor-related crontab material
  was commented out and described website deployment, not polling or reporting.
- **Verified:** The repository's `scripts/setup_cron.sh` can install
  `monitor report --stateful` jobs at 15 and 45 minutes past every hour.
- **Inferred:** Cron can produce stateful reports if those repository-provided jobs
  are explicitly installed and enabled.
- **Verified:** No active production cron job was found that could currently
  produce a Registrar Monitor report.
- **Verified:** The systemd scheduler path can produce reports without cron because
  reporting is invoked from the scheduler after qualifying changes.
- **Unknown:** Whether cron reporting is intended as a fallback, an obsolete
  deployment strategy, or a separately desired reporting guarantee.

## Telegram configuration

- **Verified:** Both the generated systemd setup in the repository and the
  inspected `registrarmonitor.service` load
  `/home/dmitry_s_ivanenko/registrar_monitor/.env`.
- **Verified:** Application configuration loads the same project-local `.env` and
  reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; environment values override
  non-secret TOML defaults.
- **Verified:** The active configuration path for Telegram credentials is therefore
  `/home/dmitry_s_ivanenko/registrar_monitor/.env`.
- **Unknown:** Whether the configured bot and destination remain valid, because no
  secret values or Telegram identifiers were requested or tested.
- **Verified:** A credential-like value was present in commented service-user
  crontab material. Its value is intentionally omitted from this report.
- **Unknown:** Whether that credential has been rotated.

## Cloudflare topology and deployment

| Item | Classification | Finding |
|---|---|---|
| Cloudflare account | **Verified** | `spooktaken` |
| Pages project | **Verified** | `registrar-monitor` |
| Pages domain | **Verified** | `registrar-monitor.pages.dev` |
| Project Git integration | **Verified** | None reported by Cloudflare |
| Observed deployment environment | **Verified** | Production, branch `main` |
| Observed deployment source | **Verified** | Revision beginning `668893f` |

- **Verified:** Production authentication is supplied through
  `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` from the project-local
  environment file. No values are documented here.
- **Verified:** The observed production mechanism is a direct Cloudflare Pages
  upload. `WebsiteService.deploy()` runs Wrangler Pages deployment against the
  generated `assets/website/public` output and the configured Pages project.
- **Verified:** Scheduler journal output shows that this Pages deployment mechanism
  completed successfully on 2026-06-10.
- **Verified:** Direct Pages upload is the only supported deployment path. The
  unobserved asset-serving Worker command and entry point have been retired.

## Last successful operations

| Operation | Classification | Last success in `Asia/Almaty` | Evidence |
|---|---|---|---|
| Poll | **Verified** | 2026-06-10 15:32:18 +05 | Scheduler journal and latest Summer 2026 SQLite snapshot |
| Report | **Verified** | 2026-06-10 15:32:23 +05 | Reporting journal, `reporting_log`, and generated report file |
| Pages deployment | **Verified** | 2026-06-10 15:33:17 +05 | Scheduler/Wrangler journal and newest Cloudflare Pages deployment source |

- **Verified:** The latest SQLite snapshot has source timestamp
  `2026-06-10 15:32:16` and was committed at `2026-06-10 15:32:18 +05`.
- **Verified:** The latest reporting record references that snapshot and records a
  successful change-bearing report at `2026-06-10 15:32:23 +05`.
- **Verified:** The newest observed Pages deployment used source revision
  `668893f`, matching the deployed VPS checkout.
- **Unknown:** Cloudflare's deployment-list command returned a relative age rather
  than an exact API timestamp; the exact deployment time above is authoritative
  from the successful local deployment journal.

## Database backup and retention

- **Verified:** `DATABASE.md` documents a manual file-copy backup example, and
  `scripts/manage_database.py` exposes an operator-invoked database backup command.
- **Verified:** The CLI exposes explicit snapshot cleanup with a caller-selected
  keep count; repository documentation shows examples retaining 50 or 100
  snapshots.
- **Verified:** No Registrar Monitor backup files were found in the bounded
  production locations inspected: the project backup directories and
  `/var/backups/registrarmonitor`.
- **Verified:** No Registrar Monitor backup cron job, systemd service, systemd
  timer, Restic job, or Borg job was found in the inspected scheduler locations.
- **Verified:** `dpkg-db-backup.timer` is active, but it backs up the operating
  system package database and does not protect Registrar Monitor SQLite data.
- **Unknown:** Whether enrollment databases are protected by a Google Cloud disk
  snapshot, image policy, external backup agent, or another off-host mechanism
  outside the inspected locations.
- **Unknown:** Backup ownership, retention duration, recovery-point objective,
  recovery-time objective, encryption policy, and restore-test history.
- **Unknown:** Whether snapshot cleanup is run manually in production and, if so,
  which keep count is authoritative.

## Unresolved operator questions

1. Which systemd unit is authoritative: `registrarmonitor.service` or
   `registrar-monitor.service`?
2. Why was the scheduler stopped on 2026-06-10, and should it be running now?
3. Who owns database backups, where are they stored, and what retention, recovery
   point, and recovery time targets apply?
4. Has the credential-like value left in the commented service-user crontab been
   rotated?
5. Is direct Pages deployment through `WebsiteService` the sole supported
   production mechanism?
6. Should the alternate Worker deployment command be retained or retired?
7. When and by what controlled procedure should the stale VPS checkout be updated?
8. Should an intentional systemd stop remain stopped, or should another supervisor
   or alert require explicit acknowledgement?
9. What monitoring should alert operators when polling, reporting, deployment, or
   backups become stale?
10. Is cron reporting intended to be installed as an independent fallback, or
    should systemd remain the only production report producer?

## Safe read-only verification checklist

Run these checks from an authorized operator session. Review output before sharing
it. Never print or copy `.env`, API tokens, Telegram identifiers, process
environments, `systemctl` environment values, or unrestricted crontabs.

Set the project path without reading configuration secrets:

```bash
APP=/home/dmitry_s_ivanenko/registrar_monitor
cd "$APP"
```

### Host time

```bash
timedatectl
date --iso-8601=seconds
readlink -f /etc/localtime
```

Expected healthy evidence is an `Asia/Almaty` timezone, synchronized clock, and
active NTP.

### Processes and systemd

```bash
pgrep -a -f 'monitor (schedule|poll|run|report|deploy)' || true

for UNIT in registrarmonitor.service registrar-monitor.service; do
  systemctl is-enabled "$UNIT" 2>&1 || true
  systemctl is-active "$UNIT" 2>&1 || true
  systemctl show "$UNIT" \
    --property=ActiveState \
    --property=SubState \
    --property=UnitFileState \
    --property=User \
    --property=WorkingDirectory \
    --property=ExecStart \
    --property=EnvironmentFiles \
    --property=Restart \
    --property=RestartUSec \
    --property=NRestarts \
    --property=Result \
    --property=ExecMainStartTimestamp \
    --property=ExecMainExitTimestamp \
    --property=ExecMainStatus \
    --property=StartLimitIntervalUSec \
    --property=StartLimitBurst \
    --no-pager
done

journalctl \
  -u registrarmonitor.service \
  -u registrar-monitor.service \
  --since '30 days ago' \
  --no-pager \
  -o short-iso
```

Do not add `--property=Environment` to `systemctl show`.

### Cron

```bash
systemctl is-active cron.service 2>&1 || \
  systemctl is-active crond.service 2>&1 || true

crontab -l 2>/dev/null |
  grep -E '^[[:space:]]*([0-9*/,-]+[[:space:]]+){5}.*(monitor|registrar)' ||
  true

sudo crontab -u dmitry_s_ivanenko -l 2>/dev/null |
  grep -E '^[[:space:]]*([0-9*/,-]+[[:space:]]+){5}.*(monitor|registrar)' ||
  true
```

The anchored filter shows active scheduled command lines while excluding comments
and environment assignments. Do not publish full crontab output.

### SQLite poll and report history

The VPS may not have the `sqlite3` CLI. The following uses Python and opens each
database with SQLite URI `mode=ro`:

```bash
.venv/bin/python - <<'PY'
import glob
import sqlite3

for filename in sorted(glob.glob("data/*.db")):
    connection = sqlite3.connect(f"file:{filename}?mode=ro", uri=True)
    poll = connection.execute(
        "SELECT snapshot_id, timestamp, semester, created_at "
        "FROM snapshots ORDER BY snapshot_id DESC LIMIT 1"
    ).fetchone()
    report = connection.execute(
        "SELECT report_id, report_timestamp, changes_found, reported_snapshot_id "
        "FROM reporting_log ORDER BY report_id DESC LIMIT 1"
    ).fetchone()
    connection.close()
    print(filename, "poll=", poll, "report=", report)
PY
```

This query must remain read-only: do not omit `mode=ro`, run migrations, vacuum,
cleanup, or invoke application commands that initialize databases.

### Backup evidence

```bash
systemctl list-timers --all --no-pager |
  grep -Ei 'registrar|monitor|backup|restic|borg|sqlite' || true

for DIR in \
  "$APP/data/backups" \
  "$APP/backups" \
  /var/backups/registrarmonitor
do
  if [ -d "$DIR" ]; then
    find "$DIR" -maxdepth 2 -type f \
      -printf '%p | bytes=%s | modified=%TY-%Tm-%TdT%TH:%TM:%TS%Tz\n' |
      sort | tail -n 100
  fi
done
```

Cloud snapshots or external agents must be verified separately through their
read-only inventory APIs; absence of local backup files does not prove that no
off-host backup exists.

### Cloudflare

Load the runtime environment only into the current shell; never print it:

```bash
set -a
. "$APP/.env"
set +a

cd "$APP/assets/website"
node_modules/.bin/wrangler whoami
node_modules/.bin/wrangler pages project list
node_modules/.bin/wrangler pages deployment list \
  --project-name registrar-monitor \
  --environment production
```

Use the already installed Wrangler binary. Do not use `npx` for verification,
because it may download or update packages. Review Wrangler output before sharing
it and omit account emails or identifiers not needed for topology verification.

## Evidence boundaries

- **Verified:** Production inspection was read-only; no service, cron entry,
  database, environment file, repository checkout, or Cloudflare deployment was
  changed.
- **Verified:** Secret values and Telegram identifiers are intentionally absent
  from this document.
- **Unknown:** Conditions may have changed after the 2026-07-28 observation; rerun
  the checklist before operational decisions.
