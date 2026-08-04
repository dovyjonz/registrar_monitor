# Production topology

This is the current operator-facing description of Registrar Monitor production.
The full host was inspected on 2026-07-28; systemd, processes, and cron were
re-verified after cleanup on 2026-07-29. Step 3 database migration, Step 4
storage-mode rollout, Step 5 historical finalization, and the active Step 6 /
Step 7 runtime were verified on 2026-08-04. Runtime timestamps use
`Asia/Almaty` unless stated otherwise.

## Evidence labels

- **Verified**: directly observed in production or current repository source.
- **Inferred**: strongly supported but not directly observed.
- **Unknown**: not established by available evidence.

## Current status

| Concern | Status | Evidence |
|---|---|---|
| Monitoring | **Active** | Scheduler process and service verified running on 2026-08-04 |
| Canonical systemd unit | **Installed, enabled, active/running** | `registrarmonitor.service` |
| Obsolete systemd unit | **Removed** | `registrar-monitor.service` is absent and `LoadState=not-found` |
| Cron daemon | **Active** | `cron.service` active |
| Registrar Monitor cron jobs | **None active** | Zero matching entries for runtime user, root, SSH operator, and system cron |
| Step 3 database migration | **Applied and verified** | Five semester databases are schema v2/complete; evidence is retained under `/home/dmitry_s_ivanenko/registrar_monitor/output/migration/` |
| Step 4 storage-mode rollout | **Completed; superseded by Step 5** | All five historical databases completed serial `legacy -> shadow -> v2`; the finalization step now makes their reads v2-only |
| Step 5 historical finalization | **Finalized and verified** | All five historical databases are configured/persisted `finalized`; legacy tables retired; rollback archives retained; integrity and foreign-key checks pass |
| Step 6 Fall 2026 qualification | **Completed at bounded operational scope** | Fresh schema-v2 initialization, one controlled Fall 2026 ingest, exact legacy/v2 parity, and audited `shadow -> v2` promotion passed; the existing broad compatibility evaluator was stopped after exceeding the requested test boundary |
| Step 7 active runtime state | **Recorded** | Fall 2026 is configured/persisted `v2`; v2 reads are authoritative; `registrarmonitor.service` is active and running |
| Latest observed snapshot | **Current** | Snapshot 132 at 2026-08-04 20:34:32 +05; 132 snapshots retained |
| Latest successful report | **Current** | Snapshot 132 at 2026-08-04 20:36:55.273853 +05; changes found; Telegram disabled for the test |
| Latest successful Pages deployment | **Current** | 2026-08-04; production upload succeeded; deployment URL recorded below |
| Recurring database backup policy | **Unconfirmed** | Step 3 migration backup artifacts are verified in the evidence directory; recurring ownership and retention remain open |

Monitoring is currently active. Do not change service state or perform a
production data migration without an explicit operator request and the bounded
verification required by this runbook.

## Current code behavior

The current source compares instructor assignments semantically: HTML markup,
case, and presentation order are ignored while multi-instructor boundaries are
preserved. Presentation-only instructor changes do not create checkpoint events,
including when another section changes in the same snapshot. Stateful reporting
uses the earliest retained snapshot as the first-run comparison point when
reporting history exists but no reporting log has yet been recorded.

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
| Last inspected target revision | Updated from `origin/main` during the 2026-08-04 synchronization |
| Current reconciled checkout | Clean colocated Jujutsu `main`/`origin/main`; the current revision is discoverable with `jj log` |

The target checkout used for the Step 3 apply, Step 4 mode transitions, and
Step 5 finalizations is historical evidence based on the approved revision
`aa9334e838d951a99cf7de8cffc351cdab68b6c2`. The current VM checkout is the
reconciled `main` revision recorded below. The `.env` exists with restrictive
permissions; its contents were not printed or copied.

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
clean on `main`/`origin/main`, with an empty working-copy child for ongoing
work. The code/reporting/deployment test used `758345d9`; the documentation
follow-up is on the same `main` line. The stale VM patch is recoverable as local, untracked
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

The 2026-08-04 synchronization and reporting test are recorded below. The
canonical service was already enabled and running; this work did not change its
service state.

## 2026-08-04 synchronization and reporting test

- Local `main` and `origin/main` were pushed at commit
  `758345d91700a311a866b98c69740d02f5b4d36b` (`758345d9`). The VM fetched that
  revision, moved its local `main` bookmark, and checked out a clean empty child
  of `main`; no runtime or generated files are tracked as working-copy changes.
- The VM's `/usr/local/bin/jj` is v0.43.0 and its system Git is 2.39.5. The
  native `jj git fetch` path rejected that Git version, so synchronization used
  the supported system `git fetch origin main` followed by Jujutsu import and
  bookmark/working-copy reconciliation. Upgrade Git before relying on native
  `jj git fetch` on this host.
- `registrarmonitor.service` remained loaded, enabled, active, and running; no
  restart, stop, or enablement change was performed.
- The manual test command was
  `monitor report --stateful --no-telegram` as `dmitry_s_ivanenko`. It compared
  snapshot `132` with last reported snapshot `125`, found changes, generated
  `assets/changes/fall_2026_2026-08-04_20-34-32.txt`, and completed successfully
  without sending Telegram.
- The resulting `reporting_log_v2` row was report `23`, snapshot `132`,
  `changes_found=1`, at `2026-08-04T20:36:55.273853`. `monitor db stats`
  reported 132 snapshots, 409 courses, 893 sections, and a latest snapshot at
  `2026-08-04 20:34:32`.
- The forced website build generated five semester pages and 1,416 course-share
  pages, then uploaded successfully to Cloudflare Pages at approximately
  `2026-08-04T20:49:54+05`. Wrangler returned the production deployment URL
  [`70f9a377.registrar-monitor.pages.dev`](https://70f9a377.registrar-monitor.pages.dev).
  The VM service remained active/running after the upload.
- After the deployment, the documentation-only follow-up was pushed and the VM
  was reconciled again to `main`/`origin/main`; its working copy remained clean.

## Process supervision

### Canonical unit

`scripts/setup_vps.sh` generates `registrarmonitor.service` with:

- working directory at the project root;
- optional loading of the project-local `.env`;
- `ExecStart=/usr/bin/env uv run monitor schedule`;
- `Restart=always` with a five-second delay;
- journal output.

The installed unit is enabled and active/running. It is the only supported unit
name. `scripts/setup_vps.sh` installs dependencies and generates the unit, but
intentionally leaves enablement and startup as separate operator actions; the
current host has those actions already completed.

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
| Last observed source revision | `758345d9` |
| Latest deployment | [`70f9a377.registrar-monitor.pages.dev`](https://70f9a377.registrar-monitor.pages.dev), 2026-08-04 |

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

1. Should the VM Git client be upgraded to at least 2.41.0 so native
   `jj git fetch` is supported, or should the documented direct-fetch procedure
   remain the operational path?
2. What alert should verify the daemon's twice-hourly stateful reporter?
3. Who owns backups, retention, RPO/RTO, encryption, and restore testing?
4. Are Google Cloud disk snapshots or another off-host backup mechanism active?
5. Has the credential-like value from old commented crontab material been
   rotated?
6. What alert should detect stale polling, reporting, deployment, or backups?

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
- The 2026-07-29 post-check confirmed that historical stopped state:
  `registrarmonitor.service` was disabled and failed/inactive, the obsolete unit
  was absent, `cron.service` was active, and no Registrar Monitor workflow
  process was running. The current state is in the status table above.
- The 2026-08-04 synchronization and no-Telegram reporting test updated the VM
  checkout to `758345d9`; snapshot 132 was reported successfully and the
  canonical service remained active/running. No service-state change occurred.
- The 2026-08-02 permissions/tooling repair did not modify the canonical unit,
  cron configuration, databases, runtime data, environment file, or application
  processes; it intentionally reconciled only the checkout permissions and
  stale tracked patch described above.
- SSH access caused gcloud to update project SSH metadata for the operator
  account; no application runtime state was changed by that access setup.
- Re-run the bounded checks before future operational decisions.
