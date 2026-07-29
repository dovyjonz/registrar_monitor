---
status: proposed
---

# Checkpoint enrollment state with events and static read models

Registrar Monitor will retain one SQLite database per semester, but replace
finalized full-state persistence with normalized identities, materialized latest
state, append-only change events, and bounded complete checkpoints. Browser data
will remain static and will be published as a small summary plus lazy
department-history payloads behind versioned, content-addressed manifests.

This decision preserves the current and historical enrollment semantics while
reducing redundant database rows, site-generation work, and initial browser
transfer. It does not authorize a production migration or monitoring
activation.

## Context

The current schema stores every present section in `enrollment_data` for every
distinct snapshot. Consecutive identical polls are deduplicated, but a changed
snapshot still repeats the complete state of roughly one hundred sections.
Omission from a snapshot is the representation of course or section removal.

The 2026-07-29 Summer 2026 baseline contains:

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

The legacy catalog tables are mutable. Historical snapshot reconstruction
therefore projects the latest instructor, title, department, and section type
backward into older snapshots. `instructor_changes` recovers instructor
transitions, but not all catalog history. The assembled archive retains the
production XLS corpus, so migration can recover timestamp-level metadata where
coverage is complete. A semester without complete raw coverage uses the
explicit `legacy-preserving` metadata mode; unexplained state or semantic
parity failures always block cutover.

The website has no dynamic backend. Cloudflare Pages serves generated HTML,
JSON, JavaScript, and CSS, and this static operational model is intentional.

Disposable prototype runs now cover every populated historical database from
Summer 2025 through Summer 2026. The expanded corpus exposed and corrected a
schema flaw hidden by newer databases: legacy snapshot IDs are not always
chronological. Spring 2025 has 126 descending ID transitions in timestamp order
and Summer 2025 has five. Preserved IDs therefore cannot also be replay order.

The revised prototype:

- reconstructs all 3,286 tested snapshots with zero unexplained state,
  adjacent-comparison, formatted-report, course-history, checkpoint, or graph
  mismatches;
- preserves legacy snapshot and reporting IDs while ordering replay by a
  separate immutable `sequence_no`;
- passes `integrity_check`, `foreign_key_check`, source-hash preservation, and
  all four scratch recovery scenarios;
- compacts Fall 2025 to 1,961,984 bytes, Summer 2025 to 159,744 bytes, Spring
  2025 to 770,048 bytes, Spring 2026 to 2,260,992 bytes, and Summer 2026 to
  876,544 bytes;
- bounds every tested history to at most 95 states of replay;
- passes three isolated Spring-scale 100-write profiles at 8.13 ms, 8.67 ms,
  and 9.99 ms p95; the final corpus run measured 8.73 ms p95;
- adds zero bytes for warmed unchanged writes.

The assembled archive contains 5,339 XLS instances and 3,351 distinct hashes.
After exact-content deduplication, raw timestamp coverage is complete for Fall
2025 (497/497), Summer 2025 (70/70), Spring 2026 (495/495), and Summer 2026
(1,970/1,970). Spring 2025 has no timestamp match for its 254 snapshots.

Raw/database comparison found 13 one-student enrollment disagreements across
Fall 2025, Spring 2026, and Summer 2026, plus 54 Summer 2025 observations where
SQLite retains `WLL 400` section `4Int` and raw does not. These are not migration
ambiguities: SQLite remains authoritative for presence, enrollment, capacity,
and `overall_fill`. Raw XLS is used only to recover timestamp-specific catalog
metadata that the legacy schema overwrote.

Static manifest and browser delivery remain outside this data-model prototype.
The actual migration command must repeat the failure matrix before cutover;
repository work does not authorize production migration or monitoring
activation.

### Prototype recommendation classification

| Prototype recommendation | Classification | Reason |
|---|---|---|
| Fall 2025: corpus run | proceed | 497 snapshots, complete raw coverage, zero unexplained mismatches, and 1.96 MB compacted size. |
| Summer 2025: corpus run | proceed | 70 snapshots, complete raw coverage, zero unexplained mismatches, and intentional correction of legacy graph ordering. |
| Spring 2025: corpus run | proceed with legacy-metadata mode | The data model has zero unexplained mismatches after sequence ordering, but raw enrichment is unavailable for all 254 snapshots. |
| Spring 2026: corpus run | proceed | Every gate passes, including complete raw coverage, recovery, and 8.73 ms targeted changed-write p95. |
| Summer 2026: corpus run | proceed | Complete 1,970/1,970 raw coverage and zero unexplained model mismatches; Spring supplies the larger performance proof. |

### Decision reassessment

| Area | Classification | Prototype-backed conclusion |
|---|---|---|
| Schema structure and current-state ownership | accept with modification | Retain normalized identities, present-only materialized latest state, append-only events, and bounded checkpoints; add chronological `sequence_no` independent of preserved IDs. |
| Change-event semantics | accept with modification | Retain `ADD`, `UPDATE`, and `REMOVE` old/new-state shapes; distinguish normal live ingestion from compatibility backfill. |
| Added and removed courses or sections | accept | Keep explicit events and persistent catalog identity across removal and reappearance. |
| Capacity history | accept | Keep capacity in section events and checkpoints. |
| Instructor history | accept with modification | Keep instructor state in events and checkpoints, reject post-hoc legacy `instructor_changes` as temporal evidence, and use raw metadata only in a semester with complete exact timestamp coverage. |
| Checkpoint frequency and reconstruction | accept | Keep the first-state, 96-state, and 2,048-event rules. |
| Derived versus persisted values | accept with modification | Keep the current split, but do not invent historical freshness absent from legacy storage. |
| Index strategy | accept | Keep the minimal indexes. Query plans use the intended keys, and measured event ordering is not the write or read bottleneck. |
| Legacy-database compatibility | accept with modification | Preserve all legacy snapshot and reporting IDs, including duplicates, while ordering every replay and history operation by `sequence_no`. |
| Migration phases and transaction boundaries | accept with modification | Accept the atomic changed-state transaction; retain the unexercised migration phases as provisional gates. |
| Backup, rollback, and interrupted recovery | accept with modification | Scratch failure injection validates the phase-boundary design; actual migration implementation must repeat the restore gates before authorization. |
| Performance targets | accept | Keep the 10 ms changed-write target; all three isolated Spring-scale repetitions pass. |
| Operational complexity and maintenance burden | accept with modification | Retain the architecture, but make compatibility machinery temporary and require explicit retirement criteria. |
| Static manifest and browser architecture | requires more evidence | Leave the design unchanged and identify it as unevaluated by these data-model prototypes. |

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

The DDL below defines the intended logical schema. Migration implementation may
use temporary versioned table names while legacy and v2 tables coexist, but the
constraints and fields are part of this decision.

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

Legacy snapshot IDs are preserved during backfill. This includes consecutive
legacy snapshots with identical canonical state: Summer contains 1,412 such
snapshots and Spring contains 18. Compatibility backfill creates their v2
snapshot rows so every reporting reference and application-visible snapshot ID
remains valid. This is a migration-only exception to live deduplication; it
must not create course or section events for an unchanged canonical state.

`sequence_no` is assigned in ascending `observed_at` order during backfill and
is the only ordering key for replay, checkpoints, latest-state selection,
history generation, and event application. It is immutable and contiguous
within a semester database. `snapshot_id` remains an external compatibility
identity and must never be used as chronological order. This distinction is
required by Spring 2025 and Summer 2025 evidence.

Dual writes use the same snapshot ID in legacy and v2 storage so reporting
state and existing application-level interfaces do not require an ID
translation layer.

### Checkpoint rule

A complete checkpoint is created:

- for the first distinct state;
- for the first state that reaches either 96 distinct states or 2,048 combined
  course and section events since the previous checkpoint;
- at migration cutover;
- at migration finalization.

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

Legacy freshness coverage is mixed. Fall 2025, Summer 2025, Spring 2025, and
Spring 2026 contain `last_seen_at`; the production Summer 2026 database does
not. Compatibility backfill preserves a non-null source value after validating
that it is not earlier than `observed_at`. When the column or row value is
absent, it sets `last_seen_at` equal to `observed_at`; migration must not infer
or synthesize a wider freshness interval. Only identical observations received
after v2 ingestion begins may advance `last_seen_at`.

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
2. During normal v2 ingestion, a consecutive identical canonical state inserts
   no snapshot, event, or checkpoint. It updates only the newest snapshot's
  `last_seen_at`. Compatibility backfill preserves an existing legacy snapshot
   row, ID, and validated freshness value without creating state-change events.
3. Deduplication never changes the original `observed_at`, historical event
   times, or browser history hashes.
4. Returning to an older state after an intervening state creates a new
   snapshot even though the hash repeats.
5. Course identity is `course_code` within a semester; section identity is
   `(course, section_code)`. Removal and reappearance retain identity.
6. Every snapshot reconstructs deterministically from the nearest preceding
   checkpoint and events ordered by `sequence_no`, never by compatibility ID.
7. Latest-state tables equal reconstruction of the newest snapshot.
8. Course and section additions and removals are explicit events, not inferred
   from missing event rows.
9. Instructor removal is an explicit `old -> ""` transition. Reappearance is
   `"" -> new`; absence and an empty/TBA instructor are not conflated. Claims
   that migrated instructor history corrects legacy temporal smearing require
   complete, unambiguous raw XLS snapshot coverage; post-hoc legacy
   `instructor_changes` are diagnostic records only.
10. SQLite remains authoritative for presence, enrollment, capacity, and
    `overall_fill`. Raw XLS files are migration evidence for temporal catalog
    metadata, not a replacement source of truth. Every disagreement is counted
    and reported while the SQLite value is retained.
11. Existing `EnrollmentSnapshot`, comparison, reporting, and course-history
    interfaces retain observable semantics, except for the intentional
    correction of temporally smeared legacy metadata and graph histories that
    the legacy website malformed by ordering rows by snapshot ID.
12. A semester uses exactly one declared metadata mode: `raw-enriched` requires
    one unambiguous raw observation for every migrated snapshot;
    `legacy-preserving` copies the final legacy catalog values backward without
    claiming historical metadata accuracy. Modes are never mixed within one
    semester database.

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

## Migration and compatibility

### Operator interface

Migration introduces this explicit dry-run interface:

```text
monitor db migrate \
  --semester SEMESTER \
  --target-version 2 \
  --dry-run \
  --metadata-mode raw-enriched|legacy-preserving \
  --raw-dir PATH \
  --report PATH
```

Dry run opens the source database read-only with SQLite query-only enforcement
and builds v2 state in a disposable database. It must not create, alter, or
delete tables in the source.

The report contains:

- source and target hashes, file sizes, and row counts;
- source `last_seen_at` coverage, validation, fallback, and preservation counts;
- snapshot, event, and checkpoint counts;
- timestamp-level raw XLS coverage and ambiguity results;
- per-snapshot state parity;
- adjacent-snapshot diff parity;
- corrected instructor and catalog transitions;
- `integrity_check` and `foreign_key_check` results;
- projected static artifact sizes and generation timings.

Raw XLS files are scanned recursively and matched by embedded semester and
observation timestamp, not by filename. Exact duplicate content is collapsed;
different content at one timestamp is a conflict.

In `raw-enriched` mode, an unmatched or conflicting timestamp blocks cutover.
SQLite supplies snapshot presence, enrollment, capacity, and `overall_fill`;
the exact-matched workbook supplies title and normalized instructor metadata.
Raw/database differences are reported but the SQLite value wins. Legacy
`instructor_changes` timestamps are never reconstruction evidence.

In `legacy-preserving` mode, `--raw-dir` is omitted, the mutable final legacy
catalog values are copied for compatibility, and the report explicitly states
that historical title and instructor timing is unknown. This is the required
mode for Spring 2025 unless a complete matching raw corpus is later recovered.
It is not a reason to invent or infer metadata events.

The mode is selected per semester and recorded in the migration report and
schema metadata. A run may not silently fall back from `raw-enriched` to
`legacy-preserving`.

### Migration phases

1. **Dry run**
   - Reconstruct v2 in a disposable database.
   - Preserve legacy snapshot IDs.
   - Assign contiguous sequence numbers in ascending timestamp order.
   - Preserve validated source freshness and default only absent values.
   - Validate the declared metadata mode without fallback.
   - Prove semantic, integrity, size, and performance gates.

2. **Backup and additive backfill**
   - Keep monitoring stopped.
   - Create and verify a timestamped SQLite backup.
   - Add v2 tables without dropping or rewriting legacy tables.
   - Backfill v2 chronologically.
   - Make restart behavior explicit: an interrupted attempt must either roll
     back its active transaction or resume from a verified phase boundary
     without duplicating events or changing preserved IDs.

3. **Shadow mode**
   - Legacy reads remain authoritative.
   - Each changed observation dual-writes legacy and v2 in one transaction.
   - Every poll and site build compares relevant legacy and v2 projections.

4. **V2 read cutover**
   - Read from v2 only after shadow parity passes.
   - Continue legacy dual writes.
   - Publish the v2 static manifest while retaining the previous manifest and
     blobs.
   - Treat browser publication as a separate unvalidated gate when this
     migration includes the static read-model change.

5. **Burn-in**
   - Retain legacy tables, the verified rollback database, previous application
     configuration, and all old static artifacts for at least 14 healthy days.
   - For Fall 2026, retain them through the configured Close deadline on
     2026-08-28 at 17:30 Asia/Almaty.

6. **Final verification and shutdown**
   - Immediately before monitoring is stopped at Close, verify final state and
     diff parity, database integrity, public static output, and backup restore.
   - Stopping monitoring does not itself authorize finalization.

7. **Explicit finalization**
   - Finalization is a separate operator action performed while monitoring is
     stopped.
   - Archive the legacy full-state tables in the verified rollback database.
   - Create a compact active database with `VACUUM INTO`.
   - Verify and atomically replace the active database.
   - Never delete rollback archives automatically.

Only the atomic changed-state write boundary has prototype evidence: an
injected constraint failure rolled back snapshot metadata, events, latest
state, and checkpoint work together. Dry run, additive backfill, shadow mode,
cutover, burn-in, finalization, backup restoration, and interruption recovery
remain provisional until failure-injection tests exercise their boundaries.

### Compatibility mode

Application configuration gains a versioned `storage.mode`:

- `legacy`: legacy reads and writes;
- `shadow`: legacy authoritative reads with transactional dual writes and
  parity checks;
- `v2`: v2 reads while legacy dual writing continues until finalization.

Before finalization, rollback switches `storage.mode` to `legacy` and restores
the previous static manifest pointer. Legacy tables remain intact.

After finalization, rollback occurs only while monitoring is stopped and
restores:

- the verified rollback database;
- the prior application revision and configuration;
- the previous static manifest pointer and retained content-addressed blobs.

Rollback archives remain under operator-controlled backup retention and are
never garbage-collected by the application.

Before migration authorization, automated recovery tests must interrupt:

- additive backfill before and after a committed phase boundary;
- a dual write before commit;
- v2 read and static-manifest cutover;
- `VACUUM INTO` replacement before and after the atomic rename.

Each test must prove source-database preservation, deterministic restart,
verified backup restoration, reporting-ID continuity, and selection of the
previous static manifest when applicable.

### Google Cloud safety

Production discovery, verification, or migration follows this exact workflow:

- **Step 1**: Syntax Validation via `gcloud help compute ssh`
- **Step 2**: Parameter Verification, including explicit
  `--project=registrarmonitor`, `--zone=us-east1-c`, `--quiet`, and confirmation
  that `--dry-run` is supported
- **Step 3**: Dry-Run Command Proposal before every SSH connection
- **Step 4**: Command Proposal & Authorization; bounded read-only verification
  may proceed, while migration application, finalization, restoration, or
  service activation requires separate explicit operator authorization

Repository deployment, migration preparation, or unit installation does not
authorize enabling or starting monitoring.

## Consequences

### Positive

- Changed polls write only affected state instead of every present section.
- Identical polls retain freshness without growing logical history.
- Latest reads stay simple through materialized state.
- Historical reconstruction has bounded replay.
- Direct events replace the website's repeated full-snapshot diffs.
- Static summary and department payloads reduce initial transfer and allow
  immutable caching.
- Additive migration, dual writes, retained manifests, and verified backups
  provide multiple rollback points.

### Costs and risks

- Writes must maintain snapshots, events, latest state, and checkpoints
  transactionally.
- Migration must reconcile raw files with every historical snapshot.
- Checkpoint/event parity becomes a critical invariant requiring dedicated
  verification.
- Dual writes, parity checks, compatibility duplicate handling, failure
  recovery, and checkpoint invariants temporarily increase code, schema, and
  operating complexity.
- Content-addressed files need explicit retention and garbage-collection rules.
- Corrected temporal history may differ intentionally from current buggy legacy
  reconstruction.
- Finalization must define which shadow/dual-write paths are removed and which
  recovery procedures become permanent operator documentation. Compatibility
  machinery must not become an unowned permanent mode.

## Prototype evidence and decision log

The entries below preserve conclusions changed after the Summer and Spring
prototype runs. Decisions not listed here remain as stated above because the
prototype either supported them without qualification or did not evaluate
them.

### Compatibility identity versus chronology

- **Previous decision:** Preserve legacy `snapshot_id` and use it to order
  replay, checkpoints, histories, and latest-state selection.
- **Prototype evidence:** Spring 2025 has 126 descending ID transitions in
  timestamp order and Summer 2025 has five. ID-ordered replay lost or
  misordered states. Adding `sequence_no` restored zero-mismatch parity across
  snapshots, reports, course histories, and point reconstruction.
- **Revised decision:** Preserve `snapshot_id` only as compatibility identity.
  Assign immutable chronological `sequence_no` and use it for all ordering.
- **Consequences:** Event range queries join through `state_snapshot`; the
  unique sequence index keeps the path bounded.
- **Migration impact:** Backfill must sort by embedded database timestamp,
  reject duplicate timestamps, assign contiguous sequence numbers, and test
  non-monotonic legacy IDs.

### Raw evidence authority and metadata modes

- **Previous decision:** Complete raw coverage could correct all migrated
  snapshot state; missing coverage blocked migration.
- **Prototype evidence:** Complete raw comparison found 13 one-student
  enrollment disagreements and 54 Summer 2025 section-presence disagreements.
  Spring 2025 has no timestamp-matched raw observations.
- **Revised decision:** SQLite always wins for presence, enrollment, capacity,
  and `overall_fill`. `raw-enriched` uses complete exact XLS coverage only for
  temporal catalog metadata. `legacy-preserving` migrates semesters without
  complete raw evidence and makes no historical metadata claim.
- **Consequences:** All populated databases can use the same v2 structure, but
  migration reports must state their metadata fidelity.
- **Migration impact:** Fall 2025, Summer 2025, Spring 2026, and Summer 2026 are
  eligible for `raw-enriched`; Spring 2025 requires `legacy-preserving`.

### Legacy duplicate snapshots

- **Previous decision:** Every consecutive identical canonical state inserts no
  snapshot, while every legacy snapshot ID is preserved during backfill.
- **Prototype evidence:** These requirements conflict for 1,412 Summer snapshots
  and 18 Spring snapshots. Preserving the rows produced zero unexplained state,
  reporting, history, or replay mismatches.
- **Revised decision:** Preserve duplicate legacy snapshot rows and IDs during
  compatibility backfill, but create no state-change events for them. Continue
  deduplicating identical observations during normal v2 ingestion.
- **Consequences:** Historical compatibility remains exact, while imported
  snapshot count is not a count of distinct canonical states.
- **Tests or benchmarks required:** Assert reporting references, ordered
  reconstruction, zero events for compatibility duplicates, and live
  deduplication on both datasets.
- **Migration impact:** Backfill needs an explicit compatibility path; live and
  shadow ingestion must not use it.

### Historical freshness

- **Previous decision:** Persist `last_seen_at` and update it for consecutive
  identical observations, without defining its legacy backfill value.
- **Prototype evidence:** Four tested historical databases contain
  `last_seen_at`; Summer 2025 has five rows later than `observed_at`. The
  production Summer 2026 database has no such column.
- **Revised decision:** Preserve a non-null source `last_seen_at` after
  validating `last_seen_at >= observed_at`. When it is absent, set it to
  `observed_at`. Advance it only for identical observations processed after v2
  ingestion begins.
- **Consequences:** Migration preserves known freshness and does not invent
  missing freshness history.
- **Tests or benchmarks required:** Verify source-to-target freshness equality,
  the absent-value fallback, invalid timestamp rejection, and that the first
  live duplicate updates only `last_seen_at`.
- **Migration impact:** Backfill cannot coalesce duplicate legacy rows to infer
  freshness ranges.

### Instructor correction

- **Previous decision:** Use retained raw XLS files to correct temporally
  smeared instructor and catalog history, blocking cutover only when raw
  coverage or semantic parity is incomplete.
- **Prototype evidence:** The assembled archive provides complete unique
  timestamp coverage for Fall 2025, Summer 2025, Spring 2026, and Summer 2026.
  Spring 2026 derives 4,464 raw instructor transitions and Summer 2026 derives
  1,062 without using the post-hoc change table.
- **Revised decision:** Persist instructor state in v2 events and checkpoints,
  but reconstruct migrated instructor history from exact raw observations, not
  from legacy `instructor_changes` timestamps.
- **Consequences:** Raw replay can correct temporal smearing only in
  `raw-enriched` mode; the legacy change table remains diagnostic only.
- **Tests or benchmarks required:** Require one canonical raw observation per
  migrated snapshot, normalize instructor ordering, and fail on conflicting
  content at the same timestamp.
- **Migration impact:** Missing raw coverage selects explicit
  `legacy-preserving` mode; no inferred legacy event may be silently assigned.

### Performance and indexes

- **Previous decision:** Require changed-write p95 no greater than 10 ms and add
  no unmeasured indexes.
- **Prototype evidence:** Incremental latest-state mutation removed the original
  bottleneck. Three isolated serial 100-sample Spring runs measured 8.13 ms,
  8.67 ms, and 9.99 ms changed-write p95; the final archive run measured
  8.73 ms. Query plans show no missing material index after adding sequence
  joins.
- **Revised decision:** Require incremental latest-state mutation and keep the
  10 ms target and minimal index set.
- **Consequences:** The architecture passes the local Spring-scale release gate.
- **Tests or benchmarks required:** Repeat the same bounded benchmark on the
  deployment-class host as part of the actual migration dry run.
- **Migration impact:** Performance no longer blocks implementation; the real
  migration report must still record its host-local result.

### Migration safety

- **Previous decision:** Additive backfill, shadow mode, cutover, burn-in,
  verified backups, rollback, and atomic replacement provide safe migration and
  recovery.
- **Prototype evidence:** Scratch failure injection passed additive-backfill
  rollback/restart, failed dual-write rollback, static pointer cutover/restore,
  and `VACUUM INTO` replacement/backup restoration while preserving the source
  hash and database integrity.
- **Revised decision:** Accept the phase-boundary and restore design for
  migration implementation, but repeat the same scenarios against the actual
  migration command before authorization.
- **Consequences:** Passing data-model parity does not authorize migration or
  monitoring activation.
- **Tests or benchmarks required:** Apply the validated failure matrix to the
  actual migration implementation and verify reporting continuity and durable
  phase markers in addition to the scratch-prototype checks.
- **Migration impact:** Every tested phase needs a durable completion marker and
  an idempotent restart or rollback boundary before operator authorization.

### Operational burden

- **Previous decision:** Accept temporary dual-write complexity during a
  14-day burn-in without explicit retirement criteria.
- **Prototype evidence:** Compact storage and five-query graph generation
  justify the core model, but the prototypes did not exercise the operational
  paths that add shadow parity, compatibility handling, recovery, and static
  cutover complexity.
- **Revised decision:** Retain the staged migration, but require finalization to
  remove obsolete dual-write/shadow paths or designate each retained recovery
  path and its maintenance owner in operator documentation.
- **Consequences:** Temporary compatibility behavior cannot silently become
  permanent application architecture.
- **Tests or benchmarks required:** Verify finalization disables retired modes,
  preserves documented rollback, and leaves diagnostics able to identify the
  active storage mode and schema version.
- **Migration impact:** Finalization is incomplete until code-path retirement
  and permanent recovery ownership are verified.

## Rejected alternatives

### Keep full snapshots

This retains simple reconstruction but repeats about one hundred section rows
per changed state. The baseline uses 201,847 enrollment rows and about 16.9 MB
of table/index allocation for roughly 1,800 actual transitions.

### Latest state plus events without checkpoints

Storage is compact, but replay grows without bound and migration or recovery
validation becomes increasingly expensive.

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
backup, migration, and rollback behavior without a measured benefit.

### Keep one monolithic semester JSON

The measured payload is 353 KB and drives a 404 KB cold initial transfer even
when the user never opens history.

### Create one history blob per course

This maximizes lazy loading but creates substantially more files, manifest
entries, and invalidation churn than department-level grouping.

### Add a dynamic web backend

This violates the static Pages operational model and introduces an always-on
web process, runtime data access, scaling, monitoring, and security concerns.

### Rewrite the database destructively in place

An in-place replacement cannot provide the required read-only dry run,
transactional shadow period, or immediate pre-finalization rollback.

## Acceptance criteria

The decision may move from `proposed` to `accepted` only after an implementation
meets every applicable criterion.

### Correctness

- Reconstruct exact timestamp, presence, enrollment, capacity, `overall_fill`,
  course/section addition and removal, and adjacent-diff semantics for every
  legacy snapshot.
- Preserve legacy snapshot IDs while assigning and validating a distinct,
  contiguous chronological sequence. Test at least one non-monotonic-ID source.
- In `raw-enriched` mode, match every migrated snapshot to exactly one canonical
  raw XLS observation by embedded timestamp; otherwise block migration.
- In `legacy-preserving` mode, record incomplete raw coverage and prohibit
  inferred historical catalog changes.
- Preserve SQLite presence, enrollment, capacity, and `overall_fill` whenever
  raw XLS differs, and report every disagreement by field.
- Preserve instructor toggles and removal/reappearance transitions without
  temporal smearing or duplicate consecutive events.
- Preserve snapshot IDs and reporting-log behavior, including compatibility
  rows for consecutive duplicate legacy snapshots.
- Preserve valid source `last_seen_at` values; when absent, set them equal to
  `observed_at`. Reject values earlier than `observed_at`, and do not infer
  historical freshness.
- Pass `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.
- Leave the source SHA-256 unchanged after dry run.
- Prove latest-state equality with replay from the nearest checkpoint.
- Prove pre-finalization mode rollback and post-finalization database/static
  restoration in automated tests.
- Prove interrupted backfill, dual-write, cutover, and finalization recovery at
  explicit transaction or phase boundaries.

### Poll persistence

For 1,000 warmed identical polls:

- insert zero snapshot, event, and checkpoint rows;
- perform at most one metadata update per poll;
- preserve original observation and event timestamps;
- leave static data hashes unchanged;
- add zero bytes to the steady-state database file after warm-up;
- achieve p95 persistence latency no greater than 5 ms.

For the baseline one-section changed workload, warm write p95 must be no greater
than 10 ms. Three isolated Spring-scale prototype repetitions pass; the actual
migration dry run must repeat the measurement on its target host.

### Storage

After finalization and compaction, the representative database must be no larger
than 3.5 MB, at least 80% smaller than the 17,346,560-byte baseline.

### Website generation

- Execute no more than 25 SQL statements per semester generation.
- Achieve p95 generation time no greater than 250 ms on the baseline harness,
  compared with 1,978 statements and 872 ms.
- Emit no new blobs and leave the stable pointer unchanged for an unchanged
  build.

### Browser delivery

- Keep the stable pointer and versioned manifest together below 10 KB.
- Keep the summary payload below 100 KB uncompressed.
- Keep every department payload below 150 KB uncompressed.
- Fetch no department history during initial render.
- Fetch exactly one department payload on first access and reuse it for later
  courses in that department.
- Achieve cold navigation-to-ready p95 no greater than 125 ms.
- Keep cold initial transfer no greater than 175 KB on the existing benchmark
  environment.

These browser criteria remain unevaluated by the data-model prototypes. They
are a separate prerequisite when migration scope includes the static read-model
change.

## Targeted follow-up results and remaining experiment

The data-model prototype is complete:

- chronological sequence is separated from preserved compatibility IDs;
- all 3,286 tested snapshots have zero unexplained model mismatches;
- the assembled XLS corpus closes raw coverage for four of five populated
  semesters and defines an explicit fallback for Spring 2025;
- three isolated Spring performance repetitions pass the 10 ms gate;
- all four scratch failure-injection scenarios passed.

Do not repeat the architecture prototype. Before production cutover:

1. Implement the migration command with explicit metadata mode and durable phase
   markers.
2. Apply the defined failure matrix, semantic checks, raw classification, and
   bounded performance run to that implementation.
3. Validate static manifest and browser delivery if those changes are included
   in the same cutover.
4. Obtain separate operator authorization for production migration and, later,
   any monitoring activation.

Static manifest and browser validation remains a separate prerequisite only
when migration includes that read-model change.

## Recommendation

**Proceed to migration implementation. Do not authorize production cutover from
prototype evidence alone.**
