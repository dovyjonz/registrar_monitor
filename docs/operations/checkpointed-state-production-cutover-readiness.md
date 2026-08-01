# Checkpointed-state production cutover readiness

This document defines the evidence and operator decisions required before the
checkpointed enrollment-state design in
[`ADR-0001`](../adr/0001-checkpointed-enrollment-state.md) may be used in
production.

It is a readiness checklist, not authorization to migrate, deploy, finalize, or
start monitoring. Those actions require separate, explicit operator approval.
Monitoring must remain stopped unless that approval is given.

## Readiness states

The work advances through the following states:

1. **Design proposed**: the prototype supports the architecture, but production
   paths and recovery boundaries are not implemented.
2. **Implementation complete**: production code implements the schema, migration,
   compatibility modes, reporting, and selected website behavior.
3. **Rehearsal passed**: the actual migration command passes semantic,
   performance, interruption, backup, and restoration gates on a production
   database copy in the deployment environment.
4. **Cutover authorized**: the operator has reviewed the evidence, accepted the
   rollback plan, and explicitly authorized migration.
5. **Burn-in healthy**: v2 reads have operated for the required period while
   legacy state and rollback artifacts remain available.
6. **Finalization authorized**: a separate operator decision permits retirement
   of compatibility paths and replacement of the active database.

Passing one state does not imply authorization for the next. In particular,
repository deployment does not authorize production migration, and production
migration does not authorize service activation.

## 1. Freeze the cutover scope

Before implementation is declared complete, record the following in the
cutover evidence package:

- the application revision to deploy;
- every semester database in scope;
- the declared metadata mode for each semester:
  `raw-enriched` or `legacy-preserving`;
- whether the static manifest and lazy department payload design from ADR-0001
  is included or deferred;
- the expected application configuration before cutover, during shadow mode,
  after v2 read cutover, and after finalization;
- the operator responsible for migration, rollback, backup retention, and
  service activation.

The initial cutover must not leave the website scope implicit:

- **If the new static read model is included**, all website-generation, browser,
  publication-order, caching, and static-pointer rollback gates in ADR-0001
  apply.
- **If it is deferred**, the evidence package must explain how the existing
  public artifact contract reads v2 state, demonstrate parity with the legacy
  website, and state that manifest-v1 browser criteria are not part of this
  cutover.

Any scope change after rehearsal invalidates the affected rehearsal evidence.

## 2. Production implementation requirements

Prototype modules under `scripts/` are evidence, not production
implementation. The production package under `src/registrarmonitor` must provide
all of the following.

### Storage and schema

- The complete ADR-0001 v2 schema, constraints, indexes, checkpoint rules, and
  storage invariants.
- An explicit schema version that prevents an incompatible application revision
  from opening the database for writes.
- Chronological replay using immutable, contiguous `sequence_no`; legacy
  `snapshot_id` must remain a compatibility identity only.
- Atomic changed-state writes across snapshot metadata, events, latest-state
  projections, and optional checkpoints.
- Identical-poll handling that updates only `last_seen_at`.
- Compatibility backfill that preserves duplicate legacy snapshot rows and
  reporting references without inventing change events.
- A final checkpoint at read cutover and another at finalization.

### Application integration

- Production implementations of latest-snapshot reconstruction, historical
  reconstruction, course history, comparison, reporting-log access, and
  website projections.
- Observable parity for existing `EnrollmentSnapshot`, comparison, reporting,
  and course-history interfaces, except for the intentional historical
  corrections documented in ADR-0001.
- Scheduler and reporting behavior that cannot select or report snapshots by
  numeric snapshot-ID order.
- Diagnostics that report the active schema version, storage mode, migration
  phase, and last successful parity check without exposing secrets.

### Migration command

The application must implement the ADR-defined `monitor db migrate` interface.
The authoritative option inventory belongs in `monitor db migrate --help`, not
in this document. At minimum, the interface must support:

- a semester and target schema version;
- dry-run operation;
- an explicit metadata mode;
- a raw-XLS directory when `raw-enriched` is selected;
- a durable machine-readable and operator-readable report;
- durable phase markers for resumable or safely restartable operations.

A dry run must open the source using SQLite read-only URI mode and enable
`PRAGMA query_only`. It must build the candidate database at a separate,
operator-visible path and leave the source file's SHA-256 unchanged.

### Compatibility modes

The application configuration must support the ADR-defined modes:

- `legacy`: legacy reads and writes;
- `shadow`: legacy-authoritative reads with transactional dual writes and
  parity checks;
- `v2`: v2 reads while legacy dual writes continue.

Mode changes must be explicit and auditable. The application must refuse an
unsupported combination of application revision, schema version, migration
phase, or storage mode.

Finalization must remove or disable shadow comparison and legacy dual-write
paths from normal operation. Diagnostics must make accidental continued use of
a retired compatibility path visible.

## 3. Automated verification requirements

The production implementation must have automated tests for:

- every storage invariant in ADR-0001;
- legacy snapshot and reporting-ID preservation;
- at least one source whose snapshot IDs are not chronological;
- consecutive duplicate legacy snapshots;
- state removal, reappearance, and return from state A to B to A;
- title, department, type, instructor, enrollment, and capacity changes;
- explicit empty-instructor transitions distinct from section absence;
- checkpoint selection and replay bounds;
- latest-state equality with reconstruction from checkpoints and events;
- raw timestamp matching, exact-content deduplication, missing observations, and
  conflicting observations;
- rejection of invalid `last_seen_at`;
- metadata-mode recording and prohibition of silent mode fallback;
- reporting continuity through every compatibility mode;
- website parity or the new static read model, according to the frozen scope;
- incompatible configuration, schema, phase, and application-version failures.

The complete repository gate must pass using the authoritative `make check`
target. Migration and recovery suites not included in that target must be run
separately and named in the evidence package.

## 4. Production-shaped dry run

Run the actual production migration command on a byte-for-byte copy of each
in-scope database. The rehearsal must use:

- the application revision proposed for production;
- the production VM or an equivalent deployment-class host;
- the same filesystem type and SQLite version expected at cutover;
- the complete retained raw-XLS corpus required by the selected metadata mode;
- production-equivalent configuration with secrets and external notifications
  disabled.

The migration report must record, per semester:

- source and target paths without secret values;
- source and target SHA-256 hashes and byte sizes;
- source and target schema versions;
- metadata mode;
- raw-XLS matched, missing, ambiguous, duplicate-content, and conflicting counts;
- source `last_seen_at` coverage, rejected values, and fallback counts;
- course, section, snapshot, event, checkpoint, latest-state, and reporting-log
  row counts;
- every raw/database disagreement by field while confirming SQLite authority for
  presence, enrollment, capacity, and `overall_fill`;
- per-snapshot state parity;
- adjacent-snapshot diff parity;
- formatted-report parity;
- course-history parity;
- latest-state and checkpoint-replay equality;
- `PRAGMA integrity_check` and `PRAGMA foreign_key_check` results;
- source hash before and after dry run;
- database size and measured persistence latency;
- website generation and browser results when included in scope.

Any unexplained mismatch blocks cutover. A raw-enriched semester with a missing
or conflicting timestamp blocks cutover. The command must never silently
downgrade it to legacy-preserving mode.

## 5. Performance and size gates

Measure on the target host after warm-up:

- 1,000 identical polls insert no snapshots, events, or checkpoints, perform no
  more than one metadata update each, add zero steady-state database bytes, and
  have persistence p95 no greater than 5 ms.
- The baseline one-section changed workload has warm persistence p95 no greater
  than 10 ms.
- After finalization and compaction, the representative database is no larger
  than 3.5 MB and at least 80% smaller than the 17,346,560-byte baseline.

If the new static read model is included:

- generation executes no more than 25 SQL statements per semester;
- generation p95 is no greater than 250 ms;
- unchanged generation emits no blobs and leaves the stable pointer unchanged;
- the stable pointer and versioned manifest together remain below 10 KB;
- the summary remains below 100 KB uncompressed;
- every department payload remains below 150 KB uncompressed;
- initial rendering fetches no department history;
- first department access performs exactly one payload fetch and subsequent
  courses in that department reuse it;
- cold navigation-to-ready p95 is no greater than 125 ms;
- cold initial transfer is no greater than 175 KB in the existing benchmark
  environment.

Record the harness, iteration counts, host identity, and raw result artifact.
Averages may accompany results but do not replace the specified p95 gates.

## 6. Failure-injection and recovery gates

Exercise the following against the actual production migration implementation:

1. interruption during additive backfill before a phase-boundary commit;
2. interruption immediately after a committed backfill phase boundary;
3. a dual-write failure after work begins but before commit;
4. interruption while changing the v2 read mode;
5. interruption before and after static-pointer publication, when applicable;
6. interruption during `VACUUM INTO`;
7. interruption before the active-database atomic replacement;
8. interruption immediately after the active-database atomic replacement.

Every scenario must demonstrate:

- the source database is preserved;
- partial changed-state transactions are absent;
- restart is deterministic and does not duplicate events or change preserved
  IDs;
- the durable phase marker agrees with committed database state;
- the verified backup restores successfully;
- reporting IDs and last-reported behavior remain continuous;
- the prior application revision and configuration can reopen the restored
  database;
- the previous static manifest is selected when static publication is in scope.

The evidence package must contain the injected boundary, expected outcome,
observed outcome, artifact hashes, and pass/fail result for each scenario.

## 7. Backup and restoration readiness

Production cutover is blocked while the backup posture remains unconfirmed.
Before authorization, record:

- the backup owner;
- local and off-host backup locations;
- retention period;
- recovery point objective and recovery time objective;
- encryption and access controls;
- available disk space for the source, additive tables, candidate database,
  verified backup, and temporary `VACUUM INTO` output;
- the exact database and static artifacts covered;
- the most recent successful restoration exercise.

Immediately before migration:

1. keep monitoring stopped;
2. create a timestamped SQLite-consistent backup;
3. calculate and record its SHA-256;
4. restore it to a separate path;
5. run integrity and foreign-key checks on the restored copy;
6. compare critical row counts and the newest reconstructed state;
7. retain the verified backup until finalization and subsequent retention expiry.

The application must never delete rollback archives automatically.

## 8. Shadow-mode promotion criteria

Backfill completion alone does not permit v2 reads. Shadow mode must accumulate
all of the following evidence:

- at least 1,000 replayed unchanged observations;
- representative changed observations covering add, update, remove, and
  reappearance behavior;
- at least three stateful report cycles;
- at least three website generations;
- zero unexplained state, diff, reporting, or website parity failures;
- zero partial or divergent dual writes;
- successful database integrity and foreign-key checks after the run;
- 1,000 unchanged writes with p95 no greater than 5 ms, zero new state rows,
  and zero steady-state database growth after warm-up;
- 100 one-section changed writes through `DatabaseManager` with p95 no greater
  than 10 ms in shadow mode;
- 100 latest-snapshot v2 reads after warm-up with p95 no greater than 10 ms and
  exact result parity;
- website generation with no more than 25 SQL statements and p95 no greater
  than 250 ms;
- an operator-reviewed parity report.

Controlled replay may establish these gates while monitoring is paused. Any live
shadow observation requires separate service-activation authorization.

Use at least 10 untimed warm-up operations for each latency workload. Evidence
must retain every raw timing sample, median, nearest-rank p95, row-count and
database-size deltas, application revision, storage mode, and target-host
identity. A direct `CheckpointedStateStore` result does not substitute for the
end-to-end `DatabaseManager` shadow measurement.

An unexplained mismatch, failed dual write, corrupted phase marker, failed
integrity check, or loss of reporting continuity resets promotion readiness and
requires investigation before another attempt.

## 9. Cutover runbook requirements

Before authorization, prepare an operator runbook that gives exact, reviewed
commands for the selected application revision and environment. It must include:

- preflight verification that the canonical systemd service is stopped;
- bounded checks for unexpected scheduler, poll, report, and deploy processes;
- database, raw-corpus, application-revision, configuration, disk-space, and
  clock/timezone verification;
- backup creation and restoration verification;
- dry-run report review;
- additive backfill or migration invocation;
- entry into and verification of shadow mode;
- v2 read promotion;
- static publication and pointer verification when applicable;
- post-cutover state, reporting, website, integrity, and latency checks;
- rollback commands for every phase;
- evidence capture paths;
- the operator and approver for each state-changing step.

The runbook must use explicit Google Cloud project
`registrarmonitor`, zone `us-east1-c`, and VM
`instance-20260501-152532`. Every SSH command must first be previewed using the
validated `gcloud compute ssh --dry-run` workflow described in
[`production-topology.md`](production-topology.md).

The runbook must define:

- the planned cutover window;
- maximum allowed interruption;
- a decision deadline for rollback;
- the person empowered to abort;
- communication expectations;
- where hashes, reports, and logs are retained.

## 10. Immediate rollback conditions

Abort the current phase or return to `legacy` mode if any of the following
occurs:

- source or backup hash changes unexpectedly;
- semantic, diff, reporting, or selected website parity fails;
- raw evidence does not satisfy the declared metadata mode;
- database integrity or foreign-key checks fail;
- a phase marker and committed database state disagree;
- a dual write commits to only one representation;
- the old application cannot read the rollback database;
- reporting continuity is lost or duplicate reports would be emitted;
- the deployed website cannot select a verified manifest;
- measured latency exceeds its gate materially or repeatedly;
- required evidence cannot be captured;
- the operator cannot identify the active revision, schema version, or storage
  mode.

Before finalization, rollback means restoring `storage.mode = "legacy"` and the
previous static pointer when applicable; legacy tables remain intact.

After finalization, rollback is allowed only while monitoring is stopped and
requires restoration of the verified database backup, prior application
revision and configuration, and previous static artifacts.

## 11. Burn-in and finalization

After v2 read cutover:

- continue legacy dual writes;
- retain legacy tables, the verified rollback database, previous application
  revision and configuration, and old static artifacts;
- monitor parity, scheduler health, polling freshness, report freshness,
  deployment freshness, database integrity, latency, disk usage, and backup age;
- retain the rollback set for at least 14 healthy days;
- for Fall 2026, retain it through the configured Close deadline on
  2026-08-28 at 17:30 Asia/Almaty.

A day is healthy only if all expected polls, reports, deployments, parity checks,
and backups for that day complete or have an explained, accepted exception.
Unexplained failure pauses the healthy-day count.

Finalization requires a separate operator approval while monitoring is stopped.
Immediately beforehand, repeat:

- final legacy/v2 state and diff parity;
- reporting continuity verification;
- database integrity and foreign-key checks;
- public static-output verification;
- backup restoration verification.

Finalization then:

1. creates the required final checkpoint;
2. archives legacy full-state tables in the verified rollback database;
3. produces the compact active database with `VACUUM INTO`;
4. verifies the candidate database;
5. atomically replaces the active database;
6. verifies the replacement;
7. disables retired compatibility paths;
8. retains rollback archives under the declared operator-controlled policy.

Stopping monitoring at the semester Close deadline does not itself authorize
finalization.

## 12. Monitoring and ownership

Before any service activation, assign an owner and alerting path for:

- scheduler process failure;
- stale poll timestamps;
- stale stateful reports;
- stale or failed Pages deployments;
- shadow parity failures;
- database integrity failures;
- backup failure or excessive backup age;
- unexpected storage mode or schema version;
- disk-space exhaustion.

Alert thresholds must derive from the scheduler behavior and registrar timezone
in `settings.toml`. Secrets, Telegram identifiers, environment contents, and
complete crontabs must not appear in readiness artifacts.

## 13. Required evidence package

The cutover approver must receive:

- the accepted ADR and application revision;
- the frozen scope and semester metadata-mode table;
- successful `make check` results;
- migration and recovery test results;
- per-semester dry-run reports;
- source, candidate, backup, and restored-backup hashes;
- raw-XLS coverage and disagreement reports;
- target-host performance and size results;
- website and browser evidence, or the signed deferral decision;
- the tested cutover and rollback runbook;
- backup ownership, retention, RPO/RTO, and restore evidence;
- shadow-mode parity evidence;
- monitoring ownership and alert definitions;
- named migration, rollback, finalization, and activation approvers.

The package must identify each ADR acceptance criterion and link it to a
specific test, report, benchmark, transcript, or operator decision. “Covered by
the prototype” is not sufficient evidence for a production boundary.

## 14. Authorization record

Production migration is ready for approval only when every applicable item
above has evidence and no blocking item remains. At that point:

- change ADR-0001 from `proposed` to `accepted`;
- record the accepted implementation revision;
- record each semester's metadata mode;
- record whether the static read model is included;
- record the cutover window and rollback deadline;
- record the operator's explicit migration authorization.

Monitoring activation remains a separate authorization after migration
verification. Finalization remains another separate authorization after the
healthy burn-in and final verification.
