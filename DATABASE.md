# Database Management

This document describes the database functionality added to the Registrar Monitor system.

## Overview

The Registrar Monitor now includes SQLite database support for storing enrollment data in a normalized structure. This provides better data integrity, querying capabilities, and performance compared to the original JSON-only approach.

## Database Schema

The database consists of five main tables:

### 1. `courses`
Stores unique course information.

| Column | Type | Description |
|--------|------|-------------|
| `course_id` | INTEGER PRIMARY KEY | Auto-incrementing unique course identifier |
| `course_code` | TEXT NOT NULL UNIQUE | Course code (e.g., "CSCI 101") |
| `course_title` | TEXT | Course title (optional) |
| `department` | TEXT | Department code (e.g., "CSCI") |
| `created_at` | TIMESTAMP | When the record was created |
| `updated_at` | TIMESTAMP | When the record was last updated |

### 2. `sections`
Stores section information for each course.

| Column | Type | Description |
|--------|------|-------------|
| `section_id` | INTEGER PRIMARY KEY | Auto-incrementing unique section identifier |
| `course_id` | INTEGER | Foreign key to courses table |
| `section_code` | TEXT NOT NULL | Section code (e.g., "1L", "2R") |
| `section_type` | TEXT | Section type (e.g., "L", "R", "S") |
| `instructor` | TEXT | Instructor name (optional) |
| `created_at` | TIMESTAMP | When the record was created |
| `updated_at` | TIMESTAMP | When the record was last updated |

### 3. `snapshots`
Stores enrollment snapshot metadata.

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | INTEGER PRIMARY KEY | Auto-incrementing unique snapshot identifier |
| `timestamp` | TEXT NOT NULL UNIQUE | Snapshot timestamp (ISO format) |
| `semester` | TEXT NOT NULL | Semester name (e.g., "Fall 2025") |
| `overall_fill` | REAL NOT NULL | Overall system fill percentage (0.0-1.0+) |
| `created_at` | TIMESTAMP | When the record was created |

### 4. `enrollment_data`
Stores enrollment data for each section in each snapshot.

| Column | Type | Description |
|--------|------|-------------|
| `enrollment_id` | INTEGER PRIMARY KEY | Auto-incrementing unique enrollment record identifier |
| `snapshot_id` | INTEGER | Foreign key to snapshots table |
| `section_id` | INTEGER | Foreign key to sections table |
| `status` | TEXT | Enrollment status: "OPEN", "NEAR", or "FULL" |
| `enrollment_count` | INTEGER | Current enrollment count |
| `capacity_count` | INTEGER | Section capacity |
| `fill_percentage` | REAL | Fill percentage (0.0-1.0+) |
| `created_at` | TIMESTAMP | When the record was created |

### 5. `reporting_log`
Stores tracking information for the decoupled reporting system.

| Column | Type | Description |
|--------|------|-------------|
| `report_id` | INTEGER PRIMARY KEY | Auto-incrementing unique report identifier |
| `reported_snapshot_id` | INTEGER | Foreign key to snapshots table |
| `report_timestamp` | TEXT NOT NULL | When the report was generated (ISO format) |
| `changes_found` | INTEGER NOT NULL | Whether changes were found (1 = yes, 0 = no) |
| `created_at` | TIMESTAMP | When the record was created |

This table is used by the stateful reporter to track which snapshots have been processed and reported, preventing duplicate notifications and enabling change-based reporting.

## Status Classification

Sections are automatically classified into one of three statuses based on fill percentage:

- **FULL**: Fill percentage ≥ 100% (1.0)
- **NEAR**: Fill percentage ≥ 75% and < 100% (0.75-0.99)
- **OPEN**: Fill percentage < 75% (< 0.75)

## Usage

### Automatic Integration

The database is automatically used when processing new enrollment data. When you run the complete process or process data manually, it will:

1. Store the snapshot in the database
2. Create/update course and section records
3. Store enrollment data with automatic status classification

### Interactive Menu

Access database operations through the main menu:

```bash
uv run python -m registrarmonitor.main
# Select option 7: Database operations
```

Available operations:
- Show database statistics
- Get latest snapshot information
- Get enrollment summary for a snapshot
- Clean up old snapshots
- Migrate existing JSON files to database

### Command Line Tool

Use the standalone database management script:

```bash
# Show database statistics
python scripts/manage_database.py stats

# Migrate existing JSON files (dry run first)
python scripts/manage_database.py migrate --dry-run
python scripts/manage_database.py migrate --force

# Clean up old snapshots (keep last 30)
python scripts/manage_database.py cleanup --keep 30

# Create database backup
python scripts/manage_database.py backup data/backup.db

# Run custom queries
python scripts/manage_database.py query "SELECT COUNT(*) FROM courses"
```

## Migration from JSON

If you have existing JSON snapshot files, you can migrate them to the database:

### Through Interactive Menu
1. Run the main application
2. Select "7. Database operations"
3. Select "5. Migrate JSON files to database"

### Through Command Line
```bash
# Preview what would be migrated
python scripts/manage_database.py migrate --dry-run

# Migrate all files
python scripts/manage_database.py migrate

# Force migration (overwrite existing)
python scripts/manage_database.py migrate --force
```

## Database Location

The database file is stored in the data directory specified in `settings.toml`:
- Default location: `data/enrollment.db`
- Configurable via `directories.data_storage` setting

## Backup and Maintenance

### Creating Backups
```bash
# Using the management script
python scripts/manage_database.py backup data/backups/enrollment_backup.db

# Manual copy
cp data/enrollment.db data/backups/enrollment_$(date +%Y%m%d_%H%M%S).db
```

### Cleaning Up Old Data
```bash
# Keep only the last 50 snapshots
python scripts/manage_database.py cleanup --keep 50

# Keep only the last 20 snapshots
python scripts/manage_database.py cleanup --keep 20
```

## Error Handling

The database manager includes comprehensive error handling:

- **SQLite errors**: Database connectivity and SQL execution errors
- **Integrity errors**: Duplicate timestamps, foreign key violations
- **Data validation**: Invalid status values, missing required fields
- **File system errors**: Database file access issues

All errors are logged with appropriate detail levels.

## Performance Considerations

### Indexes
The following indexes are automatically created for optimal performance:
- `idx_courses_code` on `courses.course_code`
- `idx_sections_course_id` on `sections.course_id`
- `idx_snapshots_timestamp` on `snapshots.timestamp`
- `idx_enrollment_snapshot` on `enrollment_data.snapshot_id`
- `idx_enrollment_section` on `enrollment_data.section_id`
- `idx_reporting_log_timestamp` on `reporting_log.report_timestamp`
- `idx_reporting_log_snapshot` on `reporting_log.reported_snapshot_id`

### Query Optimization
- Use parameterized queries to prevent SQL injection
- Leverage foreign key relationships for efficient joins
- Regular cleanup prevents database bloat

## Troubleshooting

### Common Issues

**Database locked error:**
- Ensure no other processes are accessing the database
- Check file permissions on the database file

**Migration fails:**
- Verify JSON files are valid and complete
- Check available disk space
- Run with `--dry-run` first to identify issues

**Performance issues:**
- Run `VACUUM` to reclaim space: `python scripts/manage_database.py query "VACUUM"`
- Consider cleanup of old snapshots
- Check database file size and available disk space

### Logging

Enable verbose logging for debugging:
```bash
python scripts/manage_database.py --verbose stats
```

## Examples

### Common Queries

Get enrollment trends:
```sql
SELECT 
    s.timestamp,
    s.overall_fill,
    COUNT(e.enrollment_id) as total_sections,
    SUM(CASE WHEN e.status = 'FULL' THEN 1 ELSE 0 END) as full_sections
FROM snapshots s
JOIN enrollment_data e ON s.snapshot_id = e.snapshot_id
GROUP BY s.snapshot_id
ORDER BY s.timestamp DESC
LIMIT 10;
```

Find courses with highest average fill:
```sql
SELECT 
    c.course_code,
    c.department,
    AVG(e.fill_percentage) as avg_fill
FROM courses c
JOIN sections sec ON c.course_id = sec.course_id
JOIN enrollment_data e ON sec.section_id = e.section_id
JOIN snapshots s ON e.snapshot_id = s.snapshot_id
WHERE s.timestamp = (SELECT MAX(timestamp) FROM snapshots)
GROUP BY c.course_id
ORDER BY avg_fill DESC
LIMIT 10;
```

Check reporting history:
```sql
SELECT 
    rl.report_timestamp,
    s.timestamp as snapshot_timestamp,
    s.semester,
    rl.changes_found,
    s.overall_fill
FROM reporting_log rl
JOIN snapshots s ON rl.reported_snapshot_id = s.snapshot_id
ORDER BY rl.report_timestamp DESC
LIMIT 10;
```

Find unreported snapshots:
```sql
SELECT s.snapshot_id, s.timestamp, s.semester
FROM snapshots s
LEFT JOIN reporting_log rl ON s.snapshot_id = rl.reported_snapshot_id
WHERE rl.reported_snapshot_id IS NULL
ORDER BY s.timestamp DESC;
```

### API Usage

```python
from registrarmonitor.data.database_manager import DatabaseManager

# Initialize database manager
db = DatabaseManager()

# Get latest snapshot info
latest_timestamp = db.get_latest_snapshot_timestamp()
print(f"Latest snapshot: {latest_timestamp}")

# Get enrollment summary
with db.get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(snapshot_id) FROM snapshots")
    latest_id = cursor.fetchone()[0]
    
summary = db.get_enrollment_summary(latest_id)
print(f"Enrollment summary: {summary}")

# Stateful reporter usage
latest_id = db.get_latest_snapshot_id()
last_reported_id = db.get_last_reported_snapshot_id()

if latest_id != last_reported_id:
    # Get snapshot data for comparison
    current_snapshot = db.get_snapshot_data(latest_id)
    previous_snapshot = db.get_snapshot_data(last_reported_id)
    
    # After processing and sending reports
    db.add_reporting_log(latest_id, changes_were_found=True)
```
