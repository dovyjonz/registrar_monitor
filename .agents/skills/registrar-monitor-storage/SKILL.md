---
name: registrar-monitor-storage
description: Work on Registrar Monitor SQLite enrollment storage, snapshots, reporting logs, the v2 schema, reconstruction, retention, or database documentation.
---

# Registrar Monitor storage

Read `docs/architecture.md` before editing.

## Rules

- Enrollment state belongs in one SQLite database per semester.
- Preserve snapshot ordering, reporting-log semantics, and exact reconstruction.
- Bot users and delivery state belong only in `data/telegram_bot.db`.
- Use foreign keys and short transactions. Never hold a transaction across
  network or filesystem work.
- Do not add compatibility layers, JSON snapshots, or silent schema upgrades.
- New semesters are created only by `monitor db initialize`, never implicitly at runtime.

## Verification

Use temporary SQLite files for tests. Check observable reads, idempotency,
`integrity_check`, and `foreign_key_check` in proportion to the change. Run the
focused storage tests, then `make check-fast` before handoff.
