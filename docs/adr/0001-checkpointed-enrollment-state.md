---
status: accepted
---

# Checkpoint enrollment state with events and static read models

Implemented. Every semester database uses this model, and new semesters
initialize on it directly with `monitor db initialize`. Current operations are
recorded in [production topology](../operations/production-topology.md).

Registrar Monitor retains one SQLite database per semester and stores enrollment
history as normalized identities, materialized latest state, append-only change
events, and bounded complete checkpoints. Browser data remains static and is
published as a small summary plus lazy department-history payloads behind
versioned, content-addressed manifests.

This preserves enrollment semantics while reducing redundant database rows,
site-generation work, and initial browser transfer.

## Context

The earlier full-state schema stored every present section in `enrollment_data` for every
distinct snapshot. Consecutive identical polls are deduplicated, but a changed
snapshot still repeats the complete state of roughly one hundred sections.
Omission from a snapshot is the representation of course or section removal.

A 2026-07-29 Summer 2026 measurement of that schema found:

- 1,970 snapshots, 75 courses, 114 sections, and 201,847 enrollment rows;
- a 17,346,560-byte SQLite database;
- about 16.9 MB allocated to `enrollment_data` and its indexes;
- approximately 1,800 actual presence, enrollment, capacity, and instructor
  transitions;
- warm latest-snapshot and course-history reads of about 1.1 ms and 6.5 ms;
- website generation with 1,978 SQL operations and a p95 of 872 ms;
- a 353 KB semester JSON payload and a 404 KB cold initial browser transfer.

SQLite read performance is already adequate. The measured problem is redundant
full-state storage and projection work, not the database engine.

The website has no dynamic backend. Cloudflare Pages serves generated HTML,
JSON, JavaScript, and CSS, and this static operational model is intentional.

## Decision

### Persistence model

SQLite remains the source of truth, with one database per semester. A distinct
canonical state creates one lightweight snapshot row, one event per changed
course or section, latest-state projection updates, and sometimes a complete
checkpoint. A consecutive identical observation updates freshness only.

All writes for a changed state occur in one transaction:

1. insert the snapshot metadata;
2. append course and section events;
3. update or delete latest-state rows;
4. create a checkpoint when the checkpoint rule is met;
5. commit all changes atomically.

### Relational schema

The DDL below defines the logical schema. Its constraints and fields are part of
this decision.

```sql
CREATE TABLE course_catalog (
    course_id INTEGER PRIMARY KEY,
    course_code TEXT NOT NULL UNIQUE
);

CREATE TABLE section_catalog (
    section_id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES course_catalog(course_id),
    section_code TEXT NOT NULL,
    UNIQUE(course_id, section_code)
);

CREATE TABLE state_snapshot (
    snapshot_id INTEGER PRIMARY KEY,
    sequence_no INTEGER NOT NULL UNIQUE,
    observed_at TEXT NOT NULL UNIQUE,
    last_seen_at TEXT NOT NULL,
    semester TEXT NOT NULL,
    overall_fill REAL NOT NULL,
    state_hash BLOB NOT NULL CHECK(length(state_hash) = 32),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE course_latest_state (
    course_id INTEGER PRIMARY KEY REFERENCES course_catalog(course_id),
    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
    title TEXT,
    department TEXT NOT NULL
);

CREATE TABLE section_latest_state (
    section_id INTEGER PRIMARY KEY REFERENCES section_catalog(section_id),
    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
    section_type TEXT NOT NULL,
    enrollment_count INTEGER NOT NULL CHECK(enrollment_count >= 0),
    capacity_count INTEGER NOT NULL CHECK(capacity_count > 0),
    instructor TEXT NOT NULL
);

CREATE TABLE course_change_event (
    event_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
    course_id INTEGER NOT NULL REFERENCES course_catalog(course_id),
    event_kind TEXT NOT NULL
        CHECK(event_kind IN ('ADD', 'UPDATE', 'REMOVE')),
    old_title TEXT,
    new_title TEXT,
    old_department TEXT,
    new_department TEXT,
    UNIQUE(snapshot_id, course_id)
);

CREATE TABLE section_change_event (
    event_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
    section_id INTEGER NOT NULL REFERENCES section_catalog(section_id),
    event_kind TEXT NOT NULL
        CHECK(event_kind IN ('ADD', 'UPDATE', 'REMOVE')),
    old_section_type TEXT,
    new_section_type TEXT,
    old_enrollment_count INTEGER,
    new_enrollment_count INTEGER,
    old_capacity_count INTEGER,
    new_capacity_count INTEGER,
    old_instructor TEXT,
    new_instructor TEXT,
    UNIQUE(snapshot_id, section_id)
);

CREATE TABLE state_checkpoint (
    checkpoint_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL UNIQUE
        REFERENCES state_snapshot(snapshot_id)
);

CREATE TABLE course_checkpoint_state (
    checkpoint_id INTEGER NOT NULL
        REFERENCES state_checkpoint(checkpoint_id),
    course_id INTEGER NOT NULL REFERENCES course_catalog(course_id),
    title TEXT,
    department TEXT NOT NULL,
    PRIMARY KEY(checkpoint_id, course_id)
);

CREATE TABLE section_checkpoint_state (
    checkpoint_id INTEGER NOT NULL
        REFERENCES state_checkpoint(checkpoint_id),
    section_id INTEGER NOT NULL REFERENCES section_catalog(section_id),
    section_type TEXT NOT NULL,
    enrollment_count INTEGER NOT NULL CHECK(enrollment_count >= 0),
    capacity_count INTEGER NOT NULL CHECK(capacity_count > 0),
    instructor TEXT NOT NULL,
    PRIMARY KEY(checkpoint_id, section_id)
);

CREATE TABLE reporting_log_v2 (
    report_id INTEGER PRIMARY KEY,
    reported_snapshot_id INTEGER NOT NULL
        REFERENCES state_snapshot(snapshot_id),
    report_timestamp TEXT NOT NULL,
    changes_found INTEGER NOT NULL CHECK(changes_found IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

`course_latest_state` and `section_latest_state` contain present entities only.
Removing the latest-state row represents current absence; the catalog identity
remains available for history and reappearance.

The `snapshot_id` on a latest-state row identifies the most recent snapshot
that changed that entity. Unchanged rows retain their existing projection and
snapshot ID; a changed write must not delete and reinsert the entire current
state.

An `ADD` event has only new state, a `REMOVE` event has only old state, and an
`UPDATE` event has both sides with at least one changed field. Implementations
must validate these shapes before insertion, even if the checks are not fully
expressed in SQLite DDL.

`sequence_no` is assigned in ascending `observed_at` order, is the only ordering
key for replay, checkpoints, latest-state selection, history generation, and
event application, and is immutable and contiguous within a semester database.
`snapshot_id` is an external identity and must never be used as chronological
order.

### Checkpoint rule

A complete checkpoint is created:

- for the first distinct state;
- for the first state that reaches either 96 distinct states or 2,048 combined
  course and section events since the previous checkpoint.

Thresholds count distinct states, not polls. A checkpoint includes every
present course and section. The hybrid threshold bounds both the number of
states and the event volume replayed during historical reconstruction.

### Persisted and derived values

Persist:

- semester-scoped course and section identities;
- course and section presence;
- enrollment, capacity, section type, instructor, title, and department;
- `observed_at`, `last_seen_at`, and `overall_fill`;
- event old and new values;
- complete checkpoints;
- the canonical SHA-256 state hash.

`last_seen_at` is never earlier than `observed_at`. Only identical observations
received after a state was first observed advance it.

Derive:

- section fill as exact `enrollment_count / capacity_count`;
- `OPEN` for fill below 0.75;
- `NEAR` for fill from 0.75 up to, but not including, 1.0;
- `FULL` for fill of at least 1.0;
- course average fill as the mean of current section fills;
- course filled state when every section of at least one present section type is
  full;
- course enrollment and capacity totals as the minimum of the corresponding
  sums per section type;
- compact stepped histories and browser presentation labels.

`overall_fill` remains persisted. The processor calculates it from all filtered
registrar rows before normalized sections are deduplicated, so it cannot always
be reconstructed from section state.

The persisted `state_hash` is a deliberately cached derived value used for
idempotence. Its canonical input contains:

- semester;
- persisted `overall_fill`;
- sorted present courses with title and department;
- sorted present sections with type, enrollment, capacity, and normalized
  instructor.

It excludes snapshot IDs, `sequence_no`, observation timestamps,
`last_seen_at`, fill, status, course averages, browser values, and other
projections.

`state_hash` is not unique. If state A changes to B and later returns to A, the
second A is a new historical state with a new snapshot timestamp.

### Indexes

Only measured access paths receive indexes:

```sql
CREATE INDEX idx_course_change_event_course_snapshot
    ON course_change_event(course_id, snapshot_id);

CREATE INDEX idx_section_change_event_section_snapshot
    ON section_change_event(section_id, snapshot_id);

CREATE INDEX idx_reporting_log_v2_timestamp
    ON reporting_log_v2(report_timestamp);

CREATE INDEX idx_reporting_log_v2_snapshot
    ON reporting_log_v2(reported_snapshot_id);
```

The catalog `UNIQUE` constraints, `state_snapshot(sequence_no)` and
`state_snapshot(observed_at)` uniqueness, event `(snapshot_id, entity_id)`
uniqueness, checkpoint keys, and primary keys already create the remaining
indexes. SQLite can scan the unique sequence index in reverse for the latest
snapshot, so no duplicate ordering index is added.

Do not index `state_hash`, derived fill or status, low-cardinality event kinds,
or reverse checkpoint/entity order until measurements demonstrate a missing
access path. Targeted Spring query plans used the existing primary or unique
indexes for snapshot metadata, checkpoints, checkpoint state, and event ranges.
Event ordering used a temporary B-tree, but its measured phases remained below
0.34 ms p95 and were not a material read or write cost. No additional index is
justified.

### Storage invariants

The implementation must maintain all of the following:

1. A changed state is written atomically across snapshot metadata, events,
   latest state, and an optional checkpoint.
2. A consecutive identical canonical state inserts no snapshot, event, or
   checkpoint. It updates only the newest snapshot's `last_seen_at`.
3. Deduplication never changes the original `observed_at`, historical event
   times, or browser history hashes.
4. Returning to an older state after an intervening state creates a new
   snapshot even though the hash repeats.
5. Course identity is `course_code` within a semester; section identity is
   `(course, section_code)`. Removal and reappearance retain identity.
6. Every snapshot reconstructs deterministically from the nearest preceding
   checkpoint and events ordered by `sequence_no`.
7. Latest-state tables equal reconstruction of the newest snapshot.
8. Course and section additions and removals are explicit events, not inferred
   from missing event rows.
9. Instructor removal is an explicit `old -> ""` transition. Reappearance is
   `"" -> new`; absence and an empty/TBA instructor are not conflated.
10. SQLite is authoritative for presence, enrollment, capacity, and
    `overall_fill`. Raw XLS files are input evidence, not a replacement source of
    truth.
11. `EnrollmentSnapshot`, comparison, reporting, and course-history interfaces
    keep their observable semantics.

## Static browser read model

The dashboard remains a static Cloudflare Pages application. It does not gain a
dynamic API or server-side application.

### Manifest hierarchy

Each semester has a stable pointer:

```text
data/<semester>/manifest.json
```

The pointer is served with `Cache-Control: no-cache` and names the current and
previous immutable build manifests:

```text
data/<semester>/manifests/<build-id>.json
```

Data artifacts are canonical JSON addressed by the SHA-256 of their bytes:

```text
data/blobs/<sha256>.json
```

Versioned manifests and blobs are served with a one-year `immutable` cache
policy.

The stable pointer has this logical shape:

```json
{
  "manifestVersion": 1,
  "current": "manifests/<current-build-id>.json",
  "previous": "manifests/<previous-build-id>.json"
}
```

The versioned manifest contains:

```json
{
  "manifestVersion": 1,
  "dataModelVersion": 2,
  "buildId": "<build-id>",
  "semester": "Fall 2026",
  "generatedAt": "<offset-aware timestamp>",
  "currentSnapshot": {
    "id": 123,
    "observedAt": "<offset-aware timestamp>",
    "overallFill": 0.42
  },
  "summary": {
    "url": "../../blobs/<sha256>.json",
    "sha256": "<sha256>",
    "bytes": 12345
  },
  "departments": {
    "CSCI": {
      "url": "../../blobs/<sha256>.json",
      "sha256": "<sha256>",
      "bytes": 45678
    }
  }
}
```

The summary payload contains:

- current course rows and aggregate counts;
- current status and fill summaries;
- configured milestones;
- small recent-event previews;
- references needed to choose a department payload.

It contains no complete histories.

A department payload contains:

- current course and section detail for that department;
- compact stepped section and course histories;
- course, section, capacity, and instructor events;
- retired entities required to preserve removal history;
- a local timestamp table referenced by index.

The browser fetches and caches a department payload when the first course in
that department is opened. Opening another course in the same department causes
no additional department fetch.

Publication order is:

1. write any new content-addressed blobs;
2. write the immutable versioned manifest;
3. update the stable pointer last.

An unchanged build creates no blobs and leaves the stable pointer byte-for-byte
unchanged. Static rollback changes the pointer back to the previous manifest.

## Consequences

### Positive

- Changed polls write only affected state instead of every present section.
- Identical polls retain freshness without growing logical history.
- Latest reads stay simple through materialized state.
- Historical reconstruction has bounded replay.
- Direct events replace the website's repeated full-snapshot diffs.
- Static summary and department payloads reduce initial transfer and allow
  immutable caching.
- Retained manifests and verified backups provide rollback points.

### Costs and risks

- Writes must maintain snapshots, events, latest state, and checkpoints
  transactionally.
- Checkpoint/event parity becomes a critical invariant requiring dedicated
  verification.
- Content-addressed files need explicit retention and garbage-collection rules.

## Rejected alternatives

### Keep full snapshots

This retains simple reconstruction but repeats about one hundred section rows
per changed state. The baseline uses 201,847 enrollment rows and about 16.9 MB
of table/index allocation for roughly 1,800 actual transitions.

### Latest state plus events without checkpoints

Storage is compact, but replay grows without bound and recovery validation
becomes increasingly expensive.

### Checkpoints without events

Periodic full writes retain avoidable write amplification and require expensive
state comparisons to recover direct changes.

### Pure event sourcing without latest state

Every current-state read would require projection replay or another external
cache. The extra interface and recovery complexity are not justified.

### Persist fill, status, and averages

These values are deterministic projections of primitive state. Persisting them
duplicates facts and creates inconsistency risk.

### Replace SQLite

Measured warm latest-state and course-history reads are already about 1.1 ms
and 6.5 ms. No measurement justifies PostgreSQL, another embedded engine, or a
distributed store.

### Combine semesters into one database

This weakens current semester isolation and changes semester-scoped identity,
backup and rollback behavior without a measured benefit.

### Keep one monolithic semester JSON

The measured payload is 353 KB and drives a 404 KB cold initial transfer even
when the user never opens history.

### Create one history blob per course

This maximizes lazy loading but creates substantially more files, manifest
entries, and invalidation churn than department-level grouping.

### Add a dynamic web backend

This violates the static Pages operational model and introduces an always-on
web process, runtime data access, scaling, monitoring, and security concerns.

## Targets

- A warmed identical poll inserts no snapshot, event, or checkpoint row, performs
  at most one metadata update, and adds no bytes to the database file.
- A one-section changed write has a p95 persistence latency of at most 10 ms.
- Website generation executes at most 25 SQL statements per semester.
- The stable pointer and versioned manifest together stay below 10 KB, the summary
  payload below 100 KB, and every department payload below 150 KB uncompressed.
- The initial render fetches no department history; the first access fetches
  exactly one department payload and reuses it for later courses in that
  department.
