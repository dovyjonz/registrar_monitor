---
name: registrar-monitor-storage
description: Work on Registrar Monitor SQLite enrollment storage, snapshots, reporting logs, schema modes, migrations, reconstruction, retention, or database documentation.
---

# Registrar Monitor storage

Read `DATABASE.md`, `CONTEXT.md`, and the relevant ADR before editing.

## Rules

- Enrollment state belongs in one SQLite database per semester.
- Preserve snapshot ordering, reporting-log semantics, and exact reconstruction.
- Bot users and delivery state belong only in `data/telegram_bot.db`.
- Use foreign keys and short transactions. Never hold a transaction across
  network or filesystem work.
- Do not add legacy compatibility, JSON snapshots, or silent schema upgrades.
- Migration and finalization are explicit commands and separate from runtime work.

## Verification

Use temporary SQLite files for tests. Check observable reads, idempotency,
`integrity_check`, and `foreign_key_check` in proportion to the change. Run the
focused storage tests, then `make check-fast` before handoff.
