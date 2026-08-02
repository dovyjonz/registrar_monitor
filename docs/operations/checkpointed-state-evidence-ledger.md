# Checkpointed-state production evidence ledger

Updated 2026-08-02 after the authorized target-host Step 3 applies, Step 4 mode
transitions, Step 5 historical finalizations, and the stopped Fall 2026 Step 6 /
Step 7 rollout. This ledger records evidence state; it does not authorize a
deployment or service activation. Monitoring must remain stopped.

| Semester | Metadata mode | Active mode | Dry run | Backup | Apply | Shadow | Finalization / reads | Static pointer | Rollback readiness |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Summer 2025 | raw-enriched | `finalized` | Pre-existing production v2/complete state verified; not rerun | Regional snapshot verified `READY`/`UP_TO_DATE`; no new apply backup needed | Already complete before this rollout; verified, not reapplied | Completed serially; 70/70 snapshots, latest ID 58, integrity `ok`, zero foreign keys | Finalized serially; configured and persisted `finalized`; latest state unchanged; 2 checkpoints | Existing isolated pointer/browser evidence reused; not deployed | Legacy tables retired; rollback archive hash verified |
| Spring 2025 | legacy-preserving | `finalized` | Target-host dry run passed; rehearsal stopped after a disk-I/O stall per operator instruction | Production backup and restore-check verified | Applied 2026-08-02; all Step 3 gates passed | Completed serially; 254/254 snapshots, latest ID 110, integrity `ok`, zero foreign keys | Finalized serially; configured and persisted `finalized`; latest state unchanged; 4 checkpoints | Existing isolated website evidence reused; not deployed | Legacy tables retired; rollback archive hash verified |
| Fall 2025 | legacy-preserving | `finalized` | Target-host dry run passed | Production backup and restore-check verified | Applied 2026-08-02; all Step 3 gates passed | Completed serially; 497/497 snapshots, latest ID 497, integrity `ok`, zero foreign keys | Finalized serially; configured and persisted `finalized`; latest state unchanged; 8 checkpoints | Existing isolated website evidence reused; not deployed | Legacy tables retired; rollback archive hash verified |
| Spring 2026 | legacy-preserving | `finalized` | Target-host dry run passed | Production backup and restore-check verified | Applied 2026-08-02; all Step 3 gates passed | Completed serially; 495/495 snapshots, latest ID 495, integrity `ok`, zero foreign keys | Finalized serially; configured and persisted `finalized`; latest state unchanged; 8 checkpoints | Existing isolated website evidence reused; not deployed | Legacy tables retired; rollback archive hash verified |
| Summer 2026 | raw-enriched | `finalized` | Target-host dry run passed after three raw files were restored; 3,263 files scanned, 1,970 matched, 0 missing/conflicting/parse failures | Production backup and restore-check verified | Applied 2026-08-02; all Step 3 gates passed | Completed serially; 1,970/1,970 snapshots, latest ID 1,970, integrity `ok`, zero foreign keys | Finalized serially; configured and persisted `finalized`; latest state unchanged; 22 checkpoints | Existing isolated website evidence reused; not deployed | Legacy tables retired; rollback archive hash verified |
| Fall 2026 | legacy-preserving | `v2` | Fresh initializer and controlled ingest completed; sequence 1; integrity `ok`; zero foreign keys | Existing verified target protection retained; no historical migration backup required | Fresh schema-v2 initialization completed; one real registrar observation written atomically | Initialized in `shadow`; 404/404 course identities, 884/884 section identities, and latest snapshot parity passed | Promoted serially to configured/persisted `v2`; v2 `DatabaseManager` read snapshot 1 with 404 courses; legacy tables retained | Not generated or deployed | Active semester; not finalized; compatibility tables retained |

All five production databases now report `user_version=2`, SQLite integrity
`ok`, zero foreign-key violations, `migration_phase=finalized`, and active mode
`finalized`. Each finalization retained a verified rollback archive, retired
legacy tables, preserved the latest reconstructed state and reporting position,
and passed the post-finalization website read. No synthetic historical
observations were written. Deployment and service activation were not
performed.

Fall 2026 now reports `user_version=2`, SQLite integrity `ok`, zero
foreign-key violations, `migration_phase=complete`, and active mode `v2`.
The v2 and retained legacy representations have exact catalog, snapshot, and
latest-state parity. Fall 2026 was not finalized and its compatibility tables
remain enabled.

## Repository evidence

- Migration reports now include exact per-snapshot replay, adjacent-diff,
  formatted-report, course-history, reporting continuity, preserved-ID/table,
  phase-marker, integrity, foreign-key, semantic-digest, and restore-check hash
  evidence.
- `monitor db rehearse` exercises the production runner before and after every
  selected transactional phase boundary and writes an aggregate JSON/Markdown
  report plus one recovery record per boundary. `--snapshot-stride 96` covers
  the first, every 96th, and final snapshot while always retaining schema,
  catalog, reporting, and completion boundaries.
- Dry-run and rehearsal order can use `--completed-predecessor-dir` only after
  validating each predecessor candidate's schema version, completion marker,
  semester, and stored legacy fingerprint against the untouched source. Apply
  mode never accepts candidate-only predecessor evidence.
- `monitor deploy --output-dir` builds only `assets/website/src/main.js` into an
  isolated tree and refuses deployment. The production public tree is not its
  build destination.
- Browser coverage targets the deployed v3 manifest frontend and verifies manifest
  fetch order, no startup department history, one lazy department fetch with
  cache reuse, current-manifest fallback with a visible stale label, keyboard
  modal operation, focus restoration, and basic accessible naming/ID checks.

## Required evidence paths

Each semester uses its lowercase hyphenated slug in these durable locations:

- Migration dry run: `output/migration/summer-2025-dry-run.json` and `.md`
- Candidate database: `output/migration/summer-2025-candidate.db`
- Recovery aggregate: `output/migration/summer-2025-rehearsal.json` and `.md`
- Recovery scenarios: `output/migration/summer-2025-recovery/`
- Isolated generated site: `output/production-site/`
- Site crawl: `output/production-site-crawl.json`
- Browser report: `output/playwright/report/`
- Production benchmark: `output/migration/summer-2025-benchmark.json`
- Applied migration report: `output/migration/summer-2025-apply.json` and `.md`
- Fall 2026 fresh initialization report: `output/migration/fall-2026-initialize.json` and `.md`
- Fall 2026 v2 promotion report: `output/migration/fall-2026-v2.json` and `.md`

Equivalent evidence exists for `spring-2025`, `fall-2025`, `spring-2026`, and
`summer-2026`.

Each apply report records its backup and restore-check paths. The durable
production evidence folder is:

`/home/dmitry_s_ivanenko/registrar_monitor/output/migration/`

It contains the target-host JSON reports, candidate databases, and the verified
per-apply backup/restore-check pairs for Spring 2025, Fall 2025, Spring 2026,
and Summer 2026. Summer 2025 was already v2/complete and was not reapplied.
This folder is runtime output and is intentionally not tracked in the repository.

Step 5 finalization reports and verified rollback archives are retained under:

`/home/dmitry_s_ivanenko/registrar_monitor/output/migration/rollback/`

## Current production evidence boundary

The authorized Step 3, Step 4, and Step 5 run completed on 2026-08-02 against the `RUNNING` VM
`instance-20260501-152532` in `us-east1-c`. The regional standard snapshot
`registrar-monitor-pre-migration-20260801` remains `READY`/`UP_TO_DATE`; do not
create a second snapshot. The canonical `registrarmonitor.service` remains
failed/inactive and disabled, and no monitoring process was started.

The target checkout used for the apply was based on the approved revision
`aa9334e838d951a99cf7de8cffc351cdab68b6c2` (`aa9334e8`). The four
fresh-semester runtime modules were synchronized as a working-tree
implementation change for Step 6. The target raw corpus contains 3,263 `.xls`
files, including the three Summer 2026 files restored before its successful
raw-enriched dry run. The runtime `.env` was not read or changed.

The controlled Fall 2026 download observed `2026-08-02 18:49:38` and wrote one
snapshot (sequence 1; 404 courses and 884 sections) atomically to the v2 and
retained legacy representations. The target reports are retained in the
production evidence folder. The direct post-ingest parity/integrity gate and
the final v2 `DatabaseManager` read passed. The existing evaluator was
attempted once against the full raw corpus and once against the single
controlled file, but both runs exceeded the requested bounded test window and
were stopped; no evaluator report is accepted as production evidence.

The disposable target prototype evaluator was staged only under `/tmp`. Its
first run exposed and the workspace now fixes a read-only harness
`storage_mode` defect; a corrected full Spring 2026 run exceeded the bounded
work interval and was terminated. No evaluator remains, and the Spring 2026
source hash stayed `7429fbf56aaa55caecfddaea8d1464fc923f52d2e38b1c357d013eb643ca2496`.
This is not accepted v2-read evidence for the current writes.

Step 3 schema migration, Step 4 mode promotion, and the authorized Step 5
finalization are complete. The five historical databases are in active
`finalized` mode with legacy tables retired and verified rollback archives
retained; no synthetic historical writes were replayed. The local
compatibility-write performance gate remains unresolved, but it does not block
the frozen-semester finalization exception. Deployment and service activation
remain separate gates. Ongoing backup ownership, retention, RPO/RTO, and
encryption decisions remain separate from the verified migration artifacts.

## Five-semester local results

| Semester | Snapshots | Semantic SHA-256 | Recovery | Warm changed-write p95 | Performance gate |
| --- | ---: | --- | ---: | ---: | --- |
| Summer 2025 | 70 | `041bcd8973f1707afc72c668bcd1672c12e888a904e60f8d5c72b2e9a1adb162` | 12/12 | 10.104 ms | Failed |
| Spring 2025 | 254 | `766d7cadef834a41f1360926b2a150c0007dcdea67a0eb8aa5ac5344ecdc8c84` | 16/16 | 67.413 ms | Failed |
| Fall 2025 | 497 | `fb487f4209bdca3e5140fbe883eab721021a76f9c9e79450fb16d76fc2bcddb9` | 22/22 | 82.239 ms | Failed |
| Spring 2026 | 495 | `2e8f445e18090d5e789f62e0770157013a58f44b20f7430dbe6132aef7ec0836` | 22/22 | 87.125 ms | Failed |
| Summer 2026 | 3 | `a4fc7ea38eec07e13098cb6aed257564e536e9d360a7a6b89be3a64b581117cc` | 12/12 | 9.747 ms | Passed locally |

Every dry run retained legacy tables and exact course, section, snapshot, and
report IDs; reported zero semantic, adjacent-diff, formatted-report, and
course-history mismatches; preserved reporting continuity; returned SQLite
integrity `ok` with zero foreign-key violations; and left its source SHA-256
unchanged.

## Performance finding and interpretation

The failed local `warm changed-write` values above are not direct v2
migration-write measurements. `scripts/benchmark_performance.py` exercises the
complete legacy `DatabaseManager` snapshot write, including compatibility-table
`enrollment_data` insertion and the transaction commit. On the larger retained
sources, that path measured 67–87 ms p95.

The direct `CheckpointedStateStore` v2 core write on the same migration
candidates measured approximately 7–8 ms p95. That does not clear the
production gate: a disposable copy written through `DatabaseManager` in
`shadow`/`v2` mode still measured approximately 65–71 ms p95 because the legacy
tables are dual-written for compatibility. The performance gate therefore
remains unresolved until the compatibility path is optimized, or the threshold
and rollout decision are explicitly revised and the result is repeated on the
target host.

The one-time migration is a separate measurement. The Spring 2026 local dry
run took approximately 19 seconds: about 3.75 seconds was snapshot backfill and
about 10.3 seconds was verification. The benchmark connection leak that
produced `ResourceWarning`s is fixed in the current working copy; the recorded
performance artifacts predate that fix and must be regenerated. These findings
explain the benchmark symptoms but do not authorize a live apply.

## Exact metrics required for v2 promotion

Run these measurements on a disposable copy on the target VM after at least 10
untimed warm-up operations. Retain raw nanosecond samples, median, nearest-rank
p95, database sizes before/after, storage mode, application revision, and host
identity in the evidence package.

| Gate | Required result |
| --- | --- |
| Unchanged persistence | 1,000 serial observations; p95 <= 5 ms; zero new snapshot, event, or checkpoint rows; at most one freshness update per observation; zero steady-state database growth after warm-up |
| Changed persistence | 100 serial one-section changed observations; end-to-end `DatabaseManager` p95 <= 10 ms in `shadow` and again in `v2` mode |
| Latest v2 read | 100 reads after warm-up; p95 <= 10 ms; every result exactly equals the legacy/latest expected snapshot |
| Replay bound | Maximum replay distance <= 95 states, preserving the first-state, every-96-state, and 2,048-event checkpoint rules |
| Semantic parity | Zero snapshot, adjacent-diff, formatted-report, course-history, reporting-position, freshness, ID, and website-payload mismatches |
| Atomicity | Zero partial or divergent writes in the injected legacy failure, v2 failure, before-commit, and after-commit cases |
| SQLite health | `PRAGMA integrity_check` returns `ok`; `PRAGMA foreign_key_check` returns zero rows after every phase |
| Website generation | <= 25 SQL statements per semester and p95 <= 250 ms; unchanged builds publish no blobs and do not move the pointer |
| Browser delivery | Pointer plus manifest <= 10 KB, summary <= 100 KB, every department payload <= 150 KB, no department fetch at startup, one cached fetch on first department access, cold ready p95 <= 125 ms, cold transfer <= 175 KB |

The current end-to-end changed-write result fails the 10 ms requirement. Before
`legacy -> shadow`, either optimize the retained legacy compatibility write so
the same target-host harness passes, or record an explicit ADR/approver decision
with a replacement threshold and evidence that scheduler deadlines, lock
contention, and rollback safety remain acceptable. A passing direct v2-core
measurement alone is insufficient.

## Exact promotion actions

1. Keep `registrarmonitor.service` stopped. Sync the approved revision, run
   `make doctor` (or `scripts/runtime_doctor.sh` when `make` is unavailable) and
   `make check`, verify the five databases/raw corpora, and run
   target-host dry runs and bounded rehearsals in `storage.migration_order`.
2. Create and restore-check timestamped SQLite backups. Record ownership,
   off-host location, retention, RPO/RTO, free-space margin, hashes, integrity,
   and foreign-key results. The existing regional standard snapshot is already
   `READY`; verify that it remains available and do not recreate it as part of
   the migration.
3. Apply any still-unapplied schema migrations one semester at a time. Require
   schema v2, phase `complete`, mode `legacy`, exact preserved IDs, zero parity
   mismatches, and a verified predecessor marker after each apply.
4. For one semester, change its configured mode to `shadow`, run `monitor db
   mode --target-mode shadow`, then execute the quantitative shadow suite above:
   1,000 unchanged replays, 100 changed replays covering add/update/remove/
   reappearance, at least three stateful reports, and at least three isolated
   website generations. Any mismatch resets this gate.
5. After operator review of the shadow report, change the configured mode to
   `v2` and run `monitor db mode --target-mode v2`. Repeat the read, write,
   reporting, website, browser, integrity, and rollback checks before proceeding
   to the next semester.
6. After all semesters use v2 reads, retain legacy dual writes, backups, prior
   configuration/revision, and prior static artifacts for at least 14 healthy
   days and, for Fall 2026, through the Close deadline on 2026-08-28 at 17:30
   Asia/Almaty. Every expected poll, report, deployment, parity check, and backup
   must pass or have an accepted explanation.
7. With monitoring stopped and separate finalization approval, repeat parity,
   performance, website, browser, integrity, and restore checks. Finalization is
   allowed only when its compact candidate passes the v2-only operational-reader
   preflight, is <= 3.5 MB and at least 80% smaller than the 17,346,560-byte
   baseline, and the verified rollback archive is retained.

## Remaining steps to complete migration and prepare the next semester

The local candidates, recovery checks, semantic comparisons, integrity checks,
and static-site isolation checks are evidence only. Migration is complete only
after the target-host gates pass and every live database is applied and verified
in the configured order.

- [ ] **Resolve the performance gate.** Separate and record the direct v2-core
  write, one-time migration, and end-to-end legacy-compatible/shadow write
  metrics. Regenerate results with the fixed harness, then either optimize the
  compatibility path or obtain an explicit decision on a revised threshold.
  Repeat the accepted test on the target host.
- [x] **Prepare and validate the target host.** The approved revision and
  dependencies were synchronized without starting monitoring; the configured
  databases and raw corpus were verified, and target-host dry runs passed for
  the applied semesters. Standalone recovery rehearsal was not repeated after
  the operator requested that testing be minimized. v2-read, freshness, and
  website/static-pointer/browser rollout checks remain separate gates.
- [ ] **Set the backup and authorization boundary.** Decide backup ownership,
  retention, RPO/RTO, and restore evidence. The existing regional standard
  disk snapshot is `READY`; retain it and verify its identity before any live
  apply. A replacement snapshot would require separate explicit authorization.
  Monitoring must remain stopped.
- [x] **Apply the historical migrations one at a time.** Spring 2025, Fall
  2025, Spring 2026, and Summer 2026 were applied in the configured order with
  separate `--apply --authorize` invocations. Summer 2025 was already
  v2/complete and was verified without a redundant apply. Each live apply was
  followed by migration-report, backup/restore, schema, phase, integrity,
  foreign-key, parity, reporting, and ID-preservation checks. The evidence is
  retained under the production evidence folder above.
- [x] **Complete the storage rollout.** Each historical semester completed the
  approved serial `legacy -> shadow -> v2` transition on 2026-08-02 with
  monitoring stopped. The transition reports and bounded post-transition checks
  recorded configured/persisted `v2`, equal legacy/v2 snapshot counts, unchanged
  latest state, integrity `ok`, and zero foreign-key violations. Existing
  migration report and isolated website evidence was reused; no synthetic
  historical observations were written. Keep legacy tables until compatibility
  retirement has its own evidence and authorization; finalization is not an
  implicit part of the apply.
- [x] **Finalize the frozen historical semesters.** After explicit approval of
  the frozen-semester exception, Summer 2025, Spring 2025, Fall 2025, Spring
  2026, and Summer 2026 were finalized serially with monitoring stopped. Each
  database now has configured/persisted `finalized` mode, a final checkpoint,
  no legacy compatibility tables, a verified rollback archive, clean SQLite
  integrity/foreign-key checks, and successful `DatabaseManager` plus website
  reads. Fall 2026 remains active and is not finalized.
- [x] **Configure the new semester explicitly.** `settings.toml` now contains a
  configured `v2`-mode `[storage.semesters."Fall 2026"]` entry. Fall 2026
  remains outside `storage.migration_order` and was handled by the
  fresh-semester initializer rather than historical backfill.
- [x] **Validate first-use readiness at the requested bounded scope.** The fresh
  schema-v2 initializer, controlled first ingest, exact legacy/v2 parity,
  integrity/foreign-key checks, and audited `shadow -> v2` promotion passed on
  the target. The existing compatibility evaluator was attempted once against
  the full raw corpus and once against the single controlled file, but both
  exceeded the requested test window and were stopped; no evaluator report is
  accepted as production evidence. Enabling or starting
  `registrarmonitor.service` remains a separate explicit operator
  authorization.

## Step 6 and Step 7 stopped completion state

The repository is stopped with the five historical semesters finalized as
recorded above. Fall 2026 is schema v2 with configured/persisted mode `v2`, v2
as the authoritative reader, retained legacy compatibility tables, and atomic
dual writes still enabled. The controlled target ingest wrote the first real
Fall 2026 observation as sequence 1; exact parity, integrity, and foreign-key
checks passed. Fall 2026 was not finalized.

`registrarmonitor.service` remains loaded, failed/inactive, and disabled. No
normal monitoring, public website deployment, or service activation was
performed. The broad compatibility evaluator was intentionally stopped after
exceeding the requested test boundary; this is recorded as a testing
limitation, not as production data evidence.

## Raw-enriched results

- Source SHA-256: `36b36b76b02aaa9afa4b82f863dd8a080fb6b22db075f04cd1bad3d7b34928b4`
- Candidate SHA-256: `a74fc51b095639706bf0b046b27e23277f1675bdce57047dddefb76bbd732138`
- Raw evidence: 70/70 matched, 0 missing, 0 conflicting, 6 exact-content
  duplicates, 4 observations outside the snapshot set, and the 54 expected
  section-presence disagreements with SQLite remaining authoritative.
- Summer 2026 raw evidence: 3/3 matched, 0 missing, 0 conflicting, and 0
  parse failures. The archive has 2,266 observations outside the three-snapshot
  SQLite source; SQLite remains authoritative and the gap is retained as
  explicit evidence.

## Website and repository verification

- Static crawl: 1,008 pages passed from the isolated output tree; Playwright
  passed 2/2 v3 frontend scenarios.
- The 2026-08-01 full repository check passed formatting, lint, typing, 722
  Python tests (79.29% coverage), 12 frontend unit/build tests, and the
  production frontend build after the v2-only operational-reader changes.
- A Spring 2026 first-pass parity failure exposed nondeterministic set traversal
  in `SnapshotComparator`. Course and section traversal is now sorted, a
  mapping-insertion-order regression test passes, and all sampled Spring 2026
  recovery scenarios pass.
- All five historical databases now use configured and persisted `finalized`
  reads with legacy tables retired and rollback archives retained. The full
  quantitative performance gate was not rerun per the rollout instruction; its
  prior local compatibility-write result remains unresolved for active-semester
  operations and does not change the frozen-semester finalization evidence.

The protected files already present under `assets/website/public` remained
outside the Jujutsu diff. The post-apply SHA-256 values of the local database
copies are recorded below for operator comparison:

- `logs/registrar_monitor.log`: `5d254d8939c346670e4c334ba5ef71496de402055fe40d4b384b8ab505a05651`
- `logs/errors.log`: `9b8253186cc3bdbfd65c12a9c781179b375f14a5e814b3406d47e608a12889e5`
- `data/enrollment_summer_2025.db`: `5bd3eb4d570578171b7782e21b00561c2ada80df0212bf6923f8cc6dd106815e`
- `data/enrollment_spring_2025.db`: `245e21be8f5a223862232662d571264a282059bd3fc18fbfac0a0e902ab31cfe`
- `data/enrollment_fall_2025.db`: `4eb309280022b876debe8e157799dd02e9423b524b67fc16671de08274697869`
- `data/enrollment_spring_2026.db`: `d794f8a923d60721828d70e31d934e87f3d2587811318217ebdafe4735c1158c`
- `data/enrollment_summer_2026.db`: `4d11a22f6707154f676ffa8f8dec838f74578302eaa92c3f09cab8e2b5733680`
