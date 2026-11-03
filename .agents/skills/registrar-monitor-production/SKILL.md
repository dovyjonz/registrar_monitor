---
name: registrar-monitor-production
description: Safely inspect or operate the Registrar Monitor Google Cloud VM, shared permissions, systemd units, code synchronization, and production runtime. Use for VM, gcloud, SSH, permissions, services, journals, or production rollout work.
---

# Registrar Monitor production

Read the Production section of `docs/operations.md` completely before acting.

## Guardrails

- Use the global `gcloud` skill as well as this repo skill.
- Confirm the installed help for the exact leaf command.
- Always specify project `registrarmonitor` and zone `us-east1-c`.
- Preview every SSH command with `gcloud compute ssh --dry-run`.
- Keep output bounded and secret-free. Never inspect `.env`, process environments,
  Telegram IDs, or unrestricted journals.
- Default to read-only checks. Production sync, database mutation, Worker deployment,
  unit installation, and service-state changes each need explicit authorization.
- Never revive `registrar-monitor.service`.
- Units: scheduler, bot, `registrarmonitor-tunnel` (Cloudflare Tunnel for the
  webhook), `registrarmonitor-litestream` (R2 replication), and the network
  watchdog timer. Liveness is an external heartbeat (`HEARTBEAT_URL`), not a unit.
- Before deploying TLS-related changes, run `scripts/fetch_registrar_ca.sh` on the
  VM to write the registrar CA bundle; downloads never skip verification.
- Deploy code with `make deploy-vm REV=<revision> UNIT=<unit>.service` only when
  authorized; it restarts just the named unit.

## Verification

For an authorized service restart, record state and process start time before and
after, then verify the deployed source checksum. A code sync alone does not reload
Python. After permission work, verify effective ACLs and access as both operator
and runtime identities without reading file contents.
