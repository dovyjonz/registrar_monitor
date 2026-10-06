# Production topology

Last production verification recorded: 2026-08-30. Times use `Asia/Almaty` unless
noted. This is deployment evidence, not a live health check.

Service states below were checked read-only on 2026-10-06 and still match the
recorded deployment. The bot's process start remains 2026-08-30 17:48:58 +05;
the scheduler and health monitor remain disabled. This check did not inspect
database contents or change any service.

## Host

| Item | Value |
|---|---|
| Google Cloud project | `registrarmonitor` |
| VM | `instance-20260501-152532` |
| Zone | `us-east1-c` |
| OS | Debian 12 |
| Project root | `/home/dmitry_s_ivanenko/registrar_monitor` |
| Runtime user | `dmitry_s_ivanenko` |
| SSH operator | `spook` |
| Shared group | `registrarmonitor` |

## Services

| Unit | State | Purpose |
|---|---|---|
| `registrarmonitor.service` | installed, disabled, inactive/dead | scheduler, reports, dashboard publication; intentionally stopped after Fall registration closed |
| `registrarmonitor-bot.service` | installed, enabled, active/running | private subscriptions and digest delivery |
| `registrarmonitor-health.service` | installed, disabled, inactive/dead | scheduler/bot outage alerts; intentionally stopped while the scheduler is disabled |
| `registrarmonitor-network-watchdog.timer` | installed, enabled, active/waiting | checks metadata connectivity each minute and restarts `systemd-networkd` when connectivity is lost |
| `registrar-monitor.service` | retired and absent | never revive |

On 2026-08-30 the VM was synchronized to commit `dd4f0e76`, Fall 2026 was
finalized with a verified rollback archive, and the current dashboard was
uploaded to Cloudflare Pages. All six configured databases are v2-only in
`finalized` mode with no legacy compatibility tables. Future semesters initialize
directly in this mode without shadow or dual-write paths.

Fall polling remains disabled after registration closed. The scheduler and its
health monitor are loaded, disabled, and inactive. The private bot was restarted
on the deployed code at `2026-08-30 17:48:58 +05` and is active/running. The
network watchdog timer remains enabled and active/waiting; the retired
`registrar-monitor.service` remains absent.

`scripts/setup_vps.sh` generates the supported application units and the network
watchdog service/timer, but does not
install, enable, start, stop, or restart any unit. Installing or activating the
health monitor is a separate production action and is not implied by a
repository change.

## Files and permissions

Ongoing backup targets and activation gates are documented in
[backup and recovery](backup-and-recovery.md).

The operator owns source, `.git`, `.jj`, and `.venv`. The runtime user owns
generated/runtime paths including `data`, `logs`, downloads, change reports,
generated public output, and `output`. Both accounts are members of
`registrarmonitor`; shared directories are setgid and have default ACLs.

The 2026-08-24 repair recursively corrected ownership, group-write access, ACL
masks, and inheritance. Verification showed:

- the runtime user can write `README.md`, `src/registrarmonitor/main.py`, and
  `data/`;
- source and Jujutsu operation files give `registrarmonitor` effective write
  access;
- `.env` remains `dmitry_s_ivanenko:dmitry_s_ivanenko`, mode `0600`, with no
  shared ACL.

Do not broaden the checkout root or `.env` permissions to solve a nested-path
problem.

## Safe inspection

Use the `gcloud` repo skill. Validate the exact leaf command with installed help,
specify project and zone, and preview SSH with `--dry-run` before execution.

```bash
gcloud compute ssh instance-20260501-152532 \
  --project registrarmonitor \
  --zone us-east1-c \
  --quiet \
  --dry-run \
  --command="sudo -n systemctl show registrarmonitor.service --property=LoadState --property=ActiveState --property=SubState --property=UnitFileState --property=MainPID --property=ExecMainStartTimestamp --no-pager"
```

After reviewing the rendered SSH command, rerun without `--dry-run`. Keep journal
queries to a named unit and bounded time or line count. Never print `.env`, tokens,
chat IDs, process environments, or unrestricted runtime configuration.

## Change boundaries

Code sharing, VM synchronization, Pages upload, database mutation, and service
state are separate actions. Each production mutation needs explicit operator
authorization.

A code sync does not reload a running Python process. After an authorized restart,
verify unit state, process ID/start time, and source checksums. A successful unit
file copy or `daemon-reload` does not authorize enablement or startup.

## Data and deployment

- Enrollment databases and bot state live under `data/`.
- `.env` supplies Telegram and optional Cloudflare credentials.
- The dashboard deploys by direct Cloudflare Pages upload.
- The preview-image Worker is independent and does not upload Pages assets.
- Cron is not a Registrar Monitor execution path.

Historical migration/finalization evidence remains in
[`checkpointed-state-evidence-ledger.md`](checkpointed-state-evidence-ledger.md).
Tool and deployment procedures are in [`tooling.md`](tooling.md) and
[`website-publication.md`](website-publication.md).
