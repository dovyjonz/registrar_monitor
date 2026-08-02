# Database Management

This document describes the SQLite database schema and management workflows for the Registrar Monitor.

Operational diagnostics use SQLite `PRAGMA integrity_check`,
`PRAGMA foreign_key_check`, and `PRAGMA user_version`. Run `monitor doctor` for
human-readable results or `monitor doctor --json` for structured output.
Existing databases are inspected on open and are never silently upgraded or
downgraded.

Schema version 1 is the legacy normalized snapshot model. Schema version 2 is
the checkpoint/event model with the version 1 tables retained and dual-written
for one compatibility release. The per-semester rollout mode and metadata mode
are controlled in `settings.toml`.

Migration also accepts an unmarked legacy database with `PRAGMA user_version = 0`
when its normalized legacy tables have the expected columns. It validates that
shape before creating a v2 candidate or applying any change; unrelated or
malformed version-zero SQLite files are rejected.

## Overview

All enrollment data is stored in SQLite databases (one per semester, e.g., `data/enrollment_summer_2026.db`). The database provides normalized storage with better data integrity, querying, and performance compared to flat files.

## Schema

### `courses`

| Column | Type | Description |
|--------|------|-------------|
| `course_id` | INTEGER PK | Auto-incrementing ID |
| `course_code` | TEXT NOT NULL UNIQUE | e.g., "CSCI 101" |
| `course_title` | TEXT | Course title |
| `department` | TEXT | Department code |
| `created_at` | TIMESTAMP | Record creation time |
| `updated_at` | TIMESTAMP | Last update time |

### `sections`

| Column | Type | Description |
|--------|------|-------------|
| `section_id` | INTEGER PK | Auto-incrementing ID |
| `course_id` | INTEGER FK | References `courses` |
| `section_code` | TEXT NOT NULL | e.g., "1L", "2R" |
| `section_type` | TEXT | "L", "R", "S", etc. |
| `instructor` | TEXT | Instructor name |
| `created_at` | TIMESTAMP | Record creation time |
| `updated_at` | TIMESTAMP | Last update time |

### `snapshots`

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | INTEGER PK | Auto-incrementing ID |
| `timestamp` | TEXT NOT NULL UNIQUE | ISO format timestamp |
| `semester` | TEXT NOT NULL | e.g., "Summer 2026" |
| `overall_fill` | REAL NOT NULL | System fill (0.0–1.0+) |
| `created_at` | TIMESTAMP | Record creation time |

### `enrollment_data`

| Column | Type | Description |
|--------|------|-------------|
| `enrollment_id` | INTEGER PK | Auto-incrementing ID |
| `snapshot_id` | INTEGER FK | References `snapshots` |
| `section_id` | INTEGER FK | References `sections` |
| `status` | TEXT | "OPEN", "NEAR", or "FULL" |
| `enrollment_count` | INTEGER | Current enrollment |
| `capacity_count` | INTEGER | Section capacity |
| `fill_percentage` | REAL | Fill (0.0–1.0+) |
| `created_at` | TIMESTAMP | Record creation time |

### `reporting_log`

| Column | Type | Description |
|--------|------|-------------|
| `report_id` | INTEGER PK | Auto-incrementing ID |
| `reported_snapshot_id` | INTEGER FK | References `snapshots` |
| `report_timestamp` | TEXT NOT NULL | ISO format timestamp |
| `changes_found` | INTEGER NOT NULL | 1 = yes, 0 = no |
| `created_at` | TIMESTAMP | Record creation time |

Used by the stateful reporter to track which snapshots have been reported, preventing duplicate notifications.

## Status Classification

Sections are classified based on fill percentage:

| Status | Condition |
|--------|-----------|
| **FULL** | fill ≥ 100% |
| **NEAR** | 75% ≤ fill < 100% |
| **OPEN** | fill < 75% |

## CLI Commands

```bash
# Show database statistics
monitor db stats

# Clean up old snapshots (keep most recent N)
monitor db cleanup --keep 50

# Read-only rehearsal against an explicit candidate
monitor db migrate \
  --semester "Summer 2025" \
  --target-version 2 \
  --metadata-mode raw-enriched \
  --raw-dir assets/downloads \
  --database data/enrollment_summer_2025.db \
  --candidate output/summer-2025-candidate.db \
  --report output/summer-2025-migration.json \
  --dry-run

# Prove deterministic recovery at checkpoint-focused transaction boundaries
monitor db rehearse \
  --semester "Summer 2025" \
  --target-version 2 \
  --metadata-mode raw-enriched \
  --raw-dir assets/downloads \
  --database data/enrollment_summer_2025.db \
  --report output/migration/summer-2025-rehearsal.json \
  --evidence-dir output/migration/summer-2025-recovery \
  --workers 4 \
  --snapshot-stride 96

# Later dry runs/rehearsals may use verified candidates for order evidence.
# Apply mode never accepts this candidate-only predecessor path.
monitor db migrate \
  --semester "Spring 2025" \
  --target-version 2 \
  --metadata-mode legacy-preserving \
  --database data/enrollment_spring_2025.db \
  --candidate output/migration/spring-2025-candidate.db \
  --report output/migration/spring-2025-dry-run.json \
  --completed-predecessor-dir output/migration \
  --dry-run

# Apply exactly one semester (creates and restore-verifies a timestamped backup)
monitor db migrate \
  --semester "Spring 2025" \
  --target-version 2 \
  --metadata-mode legacy-preserving \
  --database data/enrollment_spring_2025.db \
  --report output/spring-2025-migration.json \
  --apply

# After changing that semester's approved mode in settings.toml
monitor db mode \
  --semester "Spring 2025" \
  --target-mode shadow \
  --report output/spring-2025-shadow.json

# Restore the prior static pointer before redeployment
monitor db rollback-manifest \
  --semester "Spring 2025" \
  --report output/spring-2025-static-rollback.json

# After the approved v2 burn-in and with monitoring stopped, compact explicitly
monitor db finalize \
  --semester "Spring 2025" \
  --rollback-dir output/migration/rollback \
  --report output/spring-2025-finalization.json \
  --authorize

# Initialize a fresh active semester after setting its approved mode to shadow
monitor db initialize \
  --semester "Fall 2026" \
  --database data/enrollment_fall_2026.db \
  --report output/migration/fall-2026-initialize.json

# After the controlled first ingest and compatibility qualification, set the
# approved mode to v2 and promote the same database.
monitor db mode \
  --semester "Fall 2026" \
  --target-mode v2 \
  --database data/enrollment_fall_2026.db \
  --report output/migration/fall-2026-v2.json
```

Migration is idempotent and resumes at committed phase markers. A completed
rerun performs semantic and SQLite verification and reports
`already_complete`. A chronological suffix written while still in legacy mode
is reconciled before shadow promotion. Marker/data disagreement, changed
preserved history, non-chronological divergence, raw-evidence conflict, or a
mode/configuration mismatch blocks the operation.

`monitor db rehearse` invokes that same production runner on disposable copies,
injects an interruption before and after schema, catalog, every snapshot,
reporting, and completion commits, then records each resumed digest and table
count in JSON and Markdown. It never applies to the source database.

`monitor db initialize` is the separate fresh-semester path. It accepts only an
empty schema-v0/v1 database (or an absent database), creates schema v2 plus the
retained legacy compatibility tables, and records a completed control row in
`shadow` mode. It does not import historical observations and never adds the
semester to `storage.migration_order`. The first controlled ingest then uses the
same atomic v2-primary/legacy-compatibility write path as an active semester.

Legacy-site verification can be generated without touching the normal public
tree by passing `monitor deploy --force --output-dir <isolated-directory>`.
An explicit output directory is generation-only: both the CLI and service
refuse Cloudflare deployment from it.

## Indexes

The following indexes are created automatically:

- `idx_courses_code` on `courses.course_code`
- `idx_sections_course_id` on `sections.course_id`
- `idx_snapshots_timestamp` on `snapshots.timestamp`
- `idx_enrollment_snapshot` on `enrollment_data.snapshot_id`
- `idx_enrollment_section` on `enrollment_data.section_id`
- `idx_reporting_log_timestamp` on `reporting_log.report_timestamp`
- `idx_reporting_log_snapshot` on `reporting_log.reported_snapshot_id`

## Backup, rollback, and retention

Apply mode uses SQLite's backup API before the first mutation. It restores that
backup into a separate verification database, runs integrity and foreign-key
checks, and compares critical row counts plus the newest reconstructed state.
The backup, restore-check database, reports, legacy tables, and static
manifests/blobs are never removed automatically.

Before compatibility finalization, rollback consists of:

1. Keep monitoring stopped.
2. Change the semester mode in `settings.toml` to `legacy`.
3. Run `monitor db mode ... --target-mode legacy`.
4. Run `monitor db rollback-manifest` when the static pointer must also revert.
5. Regenerate, validate, and redeploy the static site under separate approval.

The separate `monitor db finalize --authorize` operation is the only supported
path to `VACUUM INTO` compaction and legacy-table retirement. It first writes a
verified pre-finalization archive, records durable preparation/completion
markers, verifies the compact candidate and its reconstructed-state digest, and
atomically replaces the active database. It never deletes the archive. A
finalized database uses v2 reads and reporting only; update the semester's
approved storage mode to `finalized` when that explicit rollout state is being
recorded (the application also accepts `v2` during the handoff).

## Production Step 3, Step 4, and Step 5 evidence

The authorized 2026-08-02 production migration evidence is retained on the
runtime VM at:

`/home/dmitry_s_ivanenko/registrar_monitor/output/migration/`

The folder contains target-host dry-run and apply JSON reports, candidate
databases, verified timestamped backup/restore-check databases, serial Step 4
mode reports, and Step 5 finalization reports plus rollback archives for all
five historical semesters. Summer 2025 was already schema v2 and complete, so
it was verified rather than reapplied. This is generated runtime output and is
intentionally not tracked in the repository.

All five production databases are schema v2 with `migration_phase=finalized`,
active mode `finalized`, SQLite integrity `ok`, zero foreign-key violations,
no legacy compatibility tables, and verified rollback archives. The serial
`legacy -> shadow -> v2 -> finalized` rollout completed on 2026-08-02 while
monitoring remained stopped. Each finalization retained a final checkpoint,
preserved the reconstructed state and reporting position, and passed the
post-finalization website read. Deployment and service activation remain
separate operations.

## Example Queries

### Enrollment trends
```sql
SELECT s.timestamp, s.overall_fill,
       COUNT(e.enrollment_id) as total_sections,
       SUM(CASE WHEN e.status = 'FULL' THEN 1 ELSE 0 END) as full_sections
FROM snapshots s
JOIN enrollment_data e ON s.snapshot_id = e.snapshot_id
GROUP BY s.snapshot_id
ORDER BY s.timestamp DESC
LIMIT 10;
```

### Highest average fill courses
```sql
SELECT c.course_code, AVG(e.fill_percentage) as avg_fill
FROM courses c
JOIN sections sec ON c.course_id = sec.course_id
JOIN enrollment_data e ON sec.section_id = e.section_id
JOIN snapshots s ON e.snapshot_id = s.snapshot_id
WHERE s.timestamp = (SELECT MAX(timestamp) FROM snapshots)
GROUP BY c.course_id
ORDER BY avg_fill DESC
LIMIT 10;
```

### Reporting history
```sql
SELECT rl.report_timestamp, s.semester, rl.changes_found, s.overall_fill
FROM reporting_log rl
JOIN snapshots s ON rl.reported_snapshot_id = s.snapshot_id
ORDER BY rl.report_timestamp DESC
LIMIT 10;
```

### Unreported snapshots
```sql
SELECT s.snapshot_id, s.timestamp, s.semester
FROM snapshots s
LEFT JOIN reporting_log rl ON s.snapshot_id = rl.reported_snapshot_id
WHERE rl.reported_snapshot_id IS NULL
ORDER BY s.timestamp DESC;
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Database locked | Ensure no other processes are accessing the database |
| Performance issues | Run `VACUUM`, consider `monitor db cleanup` |

## Python API

```python
from registrarmonitor.data.database_manager import DatabaseManager

db = DatabaseManager()

# Get latest snapshot
latest_id = db.get_latest_snapshot_id()
snapshot = db.get_snapshot_data(latest_id)

# Check reporting state
last_reported_id = db.get_last_reported_snapshot_id()
if latest_id != last_reported_id:
    db.add_reporting_log(latest_id, changes_were_found=True)
```
