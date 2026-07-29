# Database Management

This document describes the SQLite database schema and management workflows for the Registrar Monitor.

Operational diagnostics use SQLite `PRAGMA integrity_check`,
`PRAGMA foreign_key_check`, and `PRAGMA user_version`. Run `monitor doctor` for
human-readable results or `monitor doctor --json` for structured output. New or
opened databases are marked with the application's expected schema version;
the diagnostic itself remains read-only.

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

```

## Indexes

The following indexes are created automatically:

- `idx_courses_code` on `courses.course_code`
- `idx_sections_course_id` on `sections.course_id`
- `idx_snapshots_timestamp` on `snapshots.timestamp`
- `idx_enrollment_snapshot` on `enrollment_data.snapshot_id`
- `idx_enrollment_section` on `enrollment_data.section_id`
- `idx_reporting_log_timestamp` on `reporting_log.report_timestamp`
- `idx_reporting_log_snapshot` on `reporting_log.reported_snapshot_id`

## Backup & Maintenance

```bash
# Manual backup
cp data/enrollment_summer_2026.db data/backups/enrollment_$(date +%Y%m%d).db

# Reclaim space
sqlite3 data/enrollment_summer_2026.db "VACUUM"
```

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
