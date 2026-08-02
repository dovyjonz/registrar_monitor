# Production topology

This is the current operator-facing description of Registrar Monitor production.
The full host was inspected on 2026-07-28; systemd, processes, and cron were
re-verified after cleanup on 2026-07-29. Step 3 database migration, Step 4
storage-mode rollout, Step 5 historical finalization, and the stopped Step 6 /
Step 7 state were verified on 2026-08-02. Runtime timestamps use
`Asia/Almaty` unless stated otherwise.

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
| Step 3 database migration | **Applied and verified** | Five semester databases are schema v2/complete; evidence is retained under `/home/dmitry_s_ivanenko/registrar_monitor/output/migration/` |
| Step 4 storage-mode rollout | **Completed; superseded by Step 5** | All five historical databases completed serial `legacy -> shadow -> v2`; the finalization step now makes their reads v2-only |
| Step 5 historical finalization | **Finalized and verified** | All five historical databases are configured/persisted `finalized`; legacy tables retired; rollback archives retained; integrity and foreign-key checks pass |
| Step 6 Fall 2026 qualification | **Completed at bounded operational scope** | Fresh schema-v2 initialization, one controlled Fall 2026 ingest, exact legacy/v2 parity, and audited `shadow -> v2` promotion passed; the existing broad compatibility evaluator was stopped after exceeding the requested test boundary |
| Step 7 stopped completion state | **Recorded** | Fall 2026 is configured/persisted `v2`; v2 reads return snapshot 1; `registrarmonitor.service` remains loaded, failed/inactive, and disabled |
| Latest successful poll | **Stale** | 2026-06-10 15:32:18 +05 |
| Latest successful report | **Stale** | 2026-06-10 15:32:23 +05 |
| Latest successful Pages deployment | **Stale** | 2026-06-10 15:33:17 +05 |
| Recurring database backup policy | **Unconfirmed** | Step 3 migration backup artifacts are verified in the evidence directory; recurring ownership and retention remain open |

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
| Migration evidence directory | `/home/dmitry_s_ivanenko/registrar_monitor/output/migration/` |
| Last inspected target revision | prefix `aa9334e8` |
| Current reconciled checkout | clean `main`/`origin/main` at `6a25c7c4`; colocated Jujutsu active working copy |

The target checkout used for the Step 3 apply, Step 4 mode transitions, and
Step 5 finalizations is based on the approved revision
`aa9334e838d951a99cf7de8cffc351cdab68b6c2`. The four fresh-semester runtime
modules were synchronized as a working-tree implementation change for Step 6;
the target's historical settings edits were preserved. The last observed Pages deployment
remains at the older source revision recorded below. The `.env` exists with
restrictive permissions; its contents were not printed or copied.

The controlled Fall 2026 download observed `2026-08-02 18:49:38`, wrote one
snapshot (sequence 1; 404 courses and 884 sections) to both representations,
and retained the initialization and v2-promotion reports under
`/home/dmitry_s_ivanenko/registrar_monitor/output/migration/`. The direct
post-ingest parity check and final `DatabaseManager` v2 read passed. The
existing evaluator was attempted once against the full raw corpus and once
against the single controlled file, but both runs exceeded the requested
bounded test window and were stopped; no evaluator report is treated as
production evidence.

## Shared runtime permissions and checkout state

The 2026-08-02 VM maintenance repaired the two-account operator/runtime model
without changing application configuration, runtime data, or service state.
The `registrarmonitor` group contains both `spook` and
`dmitry_s_ivanenko`, and new SSH sessions receive that membership.

| Path or scope | Verified ownership and access model |
|---|---|
| Runtime home and shell state | `dmitry_s_ivanenko` owns the home, `.cache`, `.config`, `.local`, `.npm`, and `.ssh`; private SSH state remains runtime-only. |
| Checkout, `.git`, `.jj`, `.venv` | `spook:registrarmonitor`, setgid and group-writable; shared source trees carry default ACLs for future files. |
| Runtime/generated paths | `data`, `logs`, downloads, change reports, generated public output, and `output` are `dmitry_s_ivanenko:registrarmonitor`, setgid and group-writable with default ACLs. |
| Checkout root | `spook:registrarmonitor` with group traverse-only access, preserving the `.env` boundary while allowing shared paths below it. |
| `.env` | `dmitry_s_ivanenko:dmitry_s_ivanenko`, mode `0600`, with no shared ACL. The operator cannot read it. |

The targeted Debian toolbox is present (`make`, `ripgrep`, `jq`, `sqlite3`,
`gh`, `acl`, `git`, `curl`, and `ca-certificates`); no general OS upgrade was
performed. The existing root-owned `/usr/local/bin/jj` was verified as
Jujutsu `v0.43.0` and retained; its SHA-256 is
`7dfff2e4416e75e5ab20eaf741d60100f43be5a9b4d18c1347364e28a765edbe`, matching
the official [v0.43.0 release](https://github.com/jj-vcs/jj/releases/tag/v0.43.0)
Linux artifact. The pinned `uv` launcher remains runtime-owned with only the
minimal group traversal/read ACL needed for the operator to run the shared
toolchain.

The checkout is now a colocated Jujutsu repository. The active working copy is
clean on `main`/`origin/main` at `6a25c7c4`, with an empty working-copy child
for ongoing work. The stale VM patch is recoverable as local, untracked
bookmark `vm-pre-reconciliation-2026-08-02` (Jujutsu change `d65d29af`), and
the secret-free binary record is retained at
`/home/dmitry_s_ivanenko/registrar_monitor/output/maintenance/`:

- `vm-working-tree.patch` — SHA-256
  `9c7040b7e2dacd17afd46cd898781b30f1edd8eb667654bf6c2e7d84135d23e6`;
- `prechange-record.txt` — identities, revision, status, and diff summary;
  pre-change service/ownership metadata supplement; SHA-256
  `1c6f67de1b537ccabe5f3cb2c1057aef277f9e8d831c2cc53660815aab54c189`;
- `postchange-record.txt` — final identities, ownership/ACL metadata, and
  service/checkout verification (no secrets or runtime contents); SHA-256
  `5fbd4c7acc1347f22431cf18f565e6317bf3c94f8442e049a3305e1279f3db32`.
- the Jujutsu-rendered archive diff is byte-identical to the saved patch and
  contains all 19 original modified paths.

No fetch, push, rebase, deployment, service activation, or database operation
was performed. `registrarmonitor.service` remains disabled and inactive/failed,
and no monitor workflow process is running; monitoring is still paused.

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

The 2026-08-02 Step 3 apply reports, verified backup/restore-check archives,
and Step 5 finalization rollback archives are retained in
`/home/dmitry_s_ivanenko/registrar_monitor/output/migration/`. This is
migration evidence, not a recurring backup policy. Backup ownership, retention,
RPO/RTO, encryption, and restore-test history outside these apply artifacts
remain unknown.

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

Run the application diagnostic as the runtime user; this path does not require
`make` and does not synchronize or install packages:

```bash
sudo -u dmitry_s_ivanenko -H \
  /home/dmitry_s_ivanenko/registrar_monitor/scripts/runtime_doctor.sh
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
- The 2026-08-02 permissions/tooling repair did not modify the canonical unit,
  cron configuration, databases, runtime data, environment file, or application
  processes; it intentionally reconciled only the checkout permissions and
  stale tracked patch described above.
- SSH access caused gcloud to update project SSH metadata for the operator
  account; no application runtime state was changed by that access setup.
- Re-run the bounded checks before future operational decisions.
