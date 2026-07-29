# Production topology

This is the current operator-facing description of Registrar Monitor production.
The full host was inspected on 2026-07-28; systemd, processes, and cron were
re-verified after cleanup on 2026-07-29. Runtime timestamps use `Asia/Almaty`
unless stated otherwise.

## Evidence labels

- **Verified**: directly observed in production or current repository source.
- **Inferred**: strongly supported but not directly observed.
- **Unknown**: not established by available evidence.

## Current status

| Concern | Status | Evidence |
|---|---|---|
| Monitoring | **Paused** | No poll, report, deploy, or scheduler process on 2026-07-29 |
| Canonical systemd unit | **Installed, disabled, failed/inactive** | `registrarmonitor.service` |
| Obsolete systemd unit | **Removed** | `registrar-monitor.service` is absent and `LoadState=not-found` |
| Cron daemon | **Active** | `cron.service` active |
| Registrar Monitor cron jobs | **None active** | Zero matching entries for runtime user, root, SSH operator, and system cron |
| Latest successful poll | **Stale** | 2026-06-10 15:32:18 +05 |
| Latest successful report | **Stale** | 2026-06-10 15:32:23 +05 |
| Latest successful Pages deployment | **Stale** | 2026-06-10 15:33:17 +05 |
| Database backups | **Unconfirmed** | No local job or backup files found in bounded locations |

Monitoring must remain paused until the planned data changes are complete and an
operator explicitly verifies them. Repository setup, code deployment, and unit
installation do not authorize activation.

## Runtime identity

| Item | Verified value |
|---|---|
| Google Cloud project | `registrarmonitor` |
| Compute Engine VM | `instance-20260501-152532` |
| Zone | `us-east1-c` |
| Host OS | Debian 12 |
| Host timezone | `Asia/Almaty` (`UTC+05`) |
| Runtime user | `dmitry_s_ivanenko` |
| Project root | `/home/dmitry_s_ivanenko/registrar_monitor` |
| Database directory | `/home/dmitry_s_ivanenko/registrar_monitor/data` |
| Environment file | `/home/dmitry_s_ivanenko/registrar_monitor/.env` |
| Last inspected deployed revision | prefix `668893f` |

The deployed checkout predates the repository revision used for this document.
The `.env` exists with restrictive permissions; its contents were not printed or
copied.

## Process supervision

### Canonical unit

`scripts/setup_vps.sh` generates `registrarmonitor.service` with:

- working directory at the project root;
- optional loading of the project-local `.env`;
- `ExecStart=/usr/bin/env uv run monitor schedule`;
- `Restart=always` with a five-second delay;
- journal output.

The installed unit is disabled and failed/inactive. It is the only supported
unit name. `scripts/setup_vps.sh` installs dependencies and generates the unit,
but intentionally does not enable or start it.

### Retired unit

The older `registrar-monitor.service` duplicated the scheduler with a different
restart delay. It was disabled on 2026-07-29 and removed from
`/etc/systemd/system/registrar-monitor.service` later that day. After
`daemon-reload` and failure-state cleanup, systemd reported it as not found.

No process was started by the cleanup.

### Scheduler behavior

`monitor schedule` polls the registrar and can trigger reporting and direct
Cloudflare Pages deployment after qualifying changes and cooldowns. Individual
workflow errors are often caught and logged; an escaping process failure should
normally be handled by systemd's restart policy.

There is no verified external alert for a stopped scheduler or stale
poll/report/deploy timestamps.

## Cron responsibilities

Cron is not a supported Registrar Monitor production path.

- `cron.service` was active on 2026-07-29.
- No active Registrar Monitor job was found for `dmitry_s_ivanenko`, `root`, the
  SSH operator account, `/etc/crontab`, or `/etc/cron.d`.
- The external cron installer has been retired.
- The systemd scheduler owns stateful reporting at minutes 15 and 45 in the
  registrar timezone. It consumes existing SQLite snapshots and does not force
  an additional poll.

Commented crontab material is not an active job. A credential-like value was
previously observed in a comment and is intentionally omitted here; rotation
remains unconfirmed.

## Data, notifications, and website

Enrollment history is stored in one SQLite database per semester under `data/`.
A legacy/default `enrollment.db` is also present. Relative paths in
`settings.toml` resolve from the project root.

Telegram credentials are read from the project-local `.env` when enabled. Their
validity has not been tested. Never expose the file, Telegram identifiers, or
process/service environments during diagnosis.

The supported website path is a direct Cloudflare Pages upload from generated
`assets/website/public` output through `WebsiteService.deploy()`.

| Item | Verified value |
|---|---|
| Pages project | `registrar-monitor` |
| Pages domain | `registrar-monitor.pages.dev` |
| Deployment mode | Direct Pages upload |
| Last observed branch | `main` |
| Last observed source revision | prefix `668893f` |

The retired Worker asset-deployment path is not supported.

## Backup posture

`DATABASE.md` documents manual copying and `scripts/manage_database.py` exposes
an operator-invoked backup command. No Registrar Monitor backup files, cron job,
systemd timer/service, Restic job, or Borg job were found in the bounded local
locations inspected on 2026-07-28.

This does not establish whether Google Cloud disk snapshots or another off-host
backup mechanism exists. Backup ownership, retention, RPO/RTO, encryption, and
restore-test history remain unknown.

## Open operator decisions

1. When and through what controlled procedure should the stale VPS checkout be
   updated?
2. When are the planned data changes complete enough to permit a separate
   service-activation decision?
3. What alert should verify the daemon's twice-hourly stateful reporter?
4. Who owns backups, retention, RPO/RTO, encryption, and restore testing?
5. Are Google Cloud disk snapshots or another off-host backup mechanism active?
6. Has the credential-like value from old commented crontab material been
   rotated?
7. What alert should detect stale polling, reporting, deployment, or backups?

## Safe verification runbook

Use the authoritative gcloud skill before running these commands. Validate the
exact leaf syntax with `gcloud help`, keep the project and zone explicit, and
preview SSH with `--dry-run`.

Connect using the account-derived OS login; the application runtime user is a
separate account:

```bash
gcloud compute ssh instance-20260501-152532 \
  --project=registrarmonitor \
  --zone=us-east1-c \
  --dry-run
```

On the host, these checks avoid secret-bearing values:

```bash
pgrep -a -f 'monitor (schedule|poll|run|report|deploy)' || true

systemctl show registrarmonitor.service \
  --property=LoadState \
  --property=ActiveState \
  --property=SubState \
  --property=UnitFileState \
  --property=FragmentPath \
  --property=Result \
  --no-pager

systemctl show registrar-monitor.service \
  --property=LoadState \
  --property=ActiveState \
  --property=SubState \
  --property=UnitFileState \
  --property=FragmentPath \
  --no-pager

systemctl is-active cron.service
```

Count active application cron entries without printing full crontabs:

```bash
for OWNER in dmitry_s_ivanenko root; do
  sudo crontab -u "$OWNER" -l 2>/dev/null |
    awk '!/^[[:space:]]*(#|$)/ && /(monitor|registrar)/ {count++}
         END {print count+0}'
done

sudo awk \
  '!/^[[:space:]]*(#|$)/ && /(monitor|registrar)/ {count++}
   END {print count+0}' \
  /etc/crontab /etc/cron.d/* 2>/dev/null
```

Do not print `.env`, `systemctl` environment properties, process environments,
unrestricted journals, or unrestricted crontabs. Inspect SQLite with read-only
URI mode (`mode=ro`) and verify cloud backups through bounded inventory queries.

## Evidence boundary

- The 2026-07-29 cleanup removed only the obsolete unit, reloaded systemd, and
  cleared that unit's stale failure state.
- A later 2026-07-29 exact-match cron retirement check found zero active
  `uv run monitor report --stateful` entries for the runtime user, root, the SSH
  operator, `/etc/crontab`, or `/etc/cron.d`; no crontab file was rewritten.
- The post-check confirmed `registrarmonitor.service` remains disabled and
  failed/inactive, the obsolete unit remains absent, `cron.service` remains
  active, and no Registrar Monitor workflow process is running.
- The canonical unit, cron configuration, databases, checkout, environment file,
  and application processes were not modified.
- SSH access caused gcloud to update project SSH metadata for the operator
  account; no application runtime state was changed by that access setup.
- Re-run the bounded checks before future operational decisions.
