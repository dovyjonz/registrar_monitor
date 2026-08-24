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
| `registrarmonitor-bot.service` | not rolled out | private subscriptions and digest delivery |
| `registrar-monitor.service` | retired and absent | never revive |

On 2026-08-24 the canonical service remained loaded, enabled, and running with
main PID `3008962` and start time `2026-08-22 20:06:48 +05`. The permission repair
did not restart, stop, deploy, or synchronize code.

`scripts/setup_vps.sh` generates both supported unit files but does not install,
enable, start, stop, or restart either unit. Installing the bot is a separate
production rollout and is not implied by this repository change.

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
