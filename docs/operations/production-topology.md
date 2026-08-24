# Production topology

Last verified: 2026-08-24. Times use `Asia/Almaty` unless noted.

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
| `registrarmonitor.service` | installed, enabled, active/running | scheduler, reports, dashboard publication |
| `registrarmonitor-bot.service` | installed, enabled, active/running | private subscriptions and digest delivery |
| `registrarmonitor-health.service` | generated; activation is separate | alerts the test operator when either main unit is not active |
| `registrar-monitor.service` | retired and absent | never revive |

On 2026-08-24 both canonical services were synchronized to commit `b83c29c` and
restarted at `2026-08-24 16:03:19 +05`. The scheduler started with main PID
`3044028`; the bot started with main PID `3044029`. Both were loaded, enabled,
and active/running after restart, their deployed subscription-source checksums
matched the local checkout, and their bounded warning journal since restart was
empty.

`scripts/setup_vps.sh` generates all three supported unit files but does not
install, enable, start, stop, or restart any unit. Installing or activating the
health monitor is a separate production action and is not implied by a
repository change.

## Files and permissions

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
