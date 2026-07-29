# Registrar XLS and database catalog — 2026-07-29

Prototype evidence was appended on 2026-07-30 after the runtime XLS and database
copies were assembled into the local checksummed archive.

This is a read-only inventory of registrar spreadsheet archives and corresponding
SQLite databases found on the laptop and production runtime. Spreadsheet
semesters and timestamps were read from the first worksheet, not inferred only
from filenames. Database ranges were read from `snapshots`.

## Executive inventory

| Location | XLS instances | Distinct SHA-256 within collection | Contents |
|---|---:|---:|---|
| Active laptop checkout `assets/downloads` | 997 | 997 | Fall 2025, Spring 2026, three inherited Summer 2025 files, three Summer 2026 files |
| Legacy laptop checkout `…/41.13 Registrar Monitor/assets/downloads` | 997 | 997 | Same 994-file core as active checkout, plus three later Spring 2026 files |
| Laptop `Downloads/assets/downloads/raw_excel` | 70 | 70 | Summer 2025 |
| Loose laptop registrar exports in `Downloads` | 15 | 15 | Fall 2023 through Fall 2026, including two school-specific Fall 2023 exports |
| Production `assets/downloads` | 3,260 | 3,260 | Fall 2025, Spring 2026, Summer 2026 |

The two 997-file laptop archives have 994 byte-identical files in common. Each
has three unique files. They are separate directories, not hard links.

Production contains 498 Fall 2025 files (404,840,960 bytes), 496 Spring 2026
files (378,188,288 bytes), and 2,266 Summer 2026 files (274,133,504 bytes).
Every production file has a distinct SHA-256 within its semester group.

## Semester matching

| Semester | Raw XLS evidence | Canonical/most complete DB | Match assessment |
|---|---|---|---|
| Summer 2026 | Production: 2,266 files, `2026-05-11 16:46:40` through `2026-06-10 15:32:15`; laptop active checkout: first three files only | Runtime `data/enrollment_summer_2026.db`; laptop exact full copy at `output/performance-input/enrollment_summer_2026-runtime.db` | Runtime DB has 1,970 snapshots, `2026-05-11 17:40:01` through `2026-06-10 15:32:16`. Not every raw poll became a distinct DB snapshot. |
| Spring 2026 | Active archive: 494 embedded Spring 2026 files; legacy archive: 497; production filename group: 496 | `data/enrollment_spring_2026.db` (laptop: 495 snapshots; runtime: 495) | Active archive matches 494/495 DB timestamps and lacks the final legacy snapshot. Legacy archive contains the complete tail plus two extra raw files. |
| Fall 2025 | 497 embedded Fall 2025 files in each laptop archive; 498 production filename-group files | `data/enrollment_fall_2025.db` (497 snapshots on laptop and runtime) | Exact 497/497 timestamp match between embedded XLS timestamps and DB snapshots. One production filename-group file embeds another semester. |
| Summer 2025 | 70 unique files in `Downloads/assets/downloads/raw_excel`, `2025-05-12 09:24:43` through `2025-05-26 00:57:59`; one earlier loose export at `2025-04-09 10:15:40` | Laptop `data/enrollment_summer_2025.db` (70 snapshots) | 69 raw timestamps match. The DB contains one later anomalous snapshot (`2025-12-18 23:10:56`) while one of the 70 raw timestamps is absent. The Telegram DB is the clean 69-snapshot through-2025-05-26 subset. Runtime `enrollment_summer_2025.db` is empty. |
| Spring 2025 | Three loose exports plus historical DB | `data/enrollment_spring_2025.db` (254 snapshots, `2024-12-09 22:17:58` through `2025-01-10 20:45:10`) | Loose files are point-in-time exports, not a complete raw archive. |
| Fall 2024 | One loose export plus DB | `data/enrollment_fall_2024.db` (one seeded snapshot) | Loose export timestamp (`2024-12-10`) does not correspond to the seeded DB timestamp (`2024-01-01`). |
| Summer 2024 | Two loose exports | No populated semester DB found | Unmatched raw exports. |
| Spring 2024 | One loose export; recovered DB variants | Recovered variant with four snapshots, `2024-01-15 09:00:00` through `11:30:00` | Loose export timestamp (`2024-12-10`) does not correspond to the recovered DB range. |
| Fall 2023 | One full loose export and two school-specific exports | No corresponding DB found | Unmatched raw exports. |
| Fall 2026 | Two full loose exports and two school-specific exports | `data/enrollment_fall_2026.db` exists but is empty | Unmatched raw exports; outside the requested historical range but related to the project. |

## Migration prototype addendum — 2026-07-30

The assembled ignored archive under `data/registrar_archive` contains 5,339 XLS
instances with 3,351 distinct SHA-256 hashes and 37 database instances with 29
distinct hashes. Prototype evaluation scans the semester-organized XLS view
recursively, collapses identical `(embedded timestamp, parsed rows)` copies, and
rejects different content at one timestamp.

SQLite remains authoritative for snapshot presence, enrollment, capacity, and
`overall_fill`. Raw XLS files are used only for timestamp-specific title and
instructor metadata. A raw/database disagreement is reported but does not
replace the SQLite fact.

| Semester database | Snapshots | Canonical raw coverage | Exact copies collapsed | Raw/SQLite differences | Sequence evidence | Prototype result |
|---|---:|---:|---:|---|---|---|
| Fall 2025 | 497 | 497/497 | 994 | 8 one-student enrollment differences | IDs chronological | Proceed, raw-enriched |
| Summer 2025 | 70 | 70/70 | 6 | 54 observations omit SQLite `WLL 400` / `4Int` | 5 descending ID transitions | Proceed, raw-enriched; correct legacy graph order |
| Spring 2025 | 254 | 0/254 | 0 | Not comparable | 126 descending ID transitions | Proceed, legacy-preserving; raw enrichment unavailable |
| Spring 2026 | 495 | 495/495 | 988 | 4 one-student enrollment differences | IDs chronological | Proceed, raw-enriched |
| Summer 2026 runtime copy | 1,970 | 1,970/1,970 | 0 | 1 one-student enrollment difference | IDs chronological | Proceed, raw-enriched |

Summer 2026 has 299 additional canonical raw observations that did not create a
database snapshot; Spring 2026 has two. They are retained evidence of polling
but are not invented migration snapshots.

The non-monotonic historical IDs require a separate chronological
`sequence_no` in the target schema. Legacy `snapshot_id` remains a compatibility
identity for reporting references and must not order replay or browser history.

Freshness coverage is also mixed. The four laptop historical databases in the
prototype corpus contain `last_seen_at`; Summer 2025 has five rows later than
their observation timestamp. The runtime-derived Summer 2026 database has no
such column. Migration must preserve validated source values and use the
observation timestamp only when freshness is absent.

## Laptop spreadsheet collections

### Active checkout

Path:
`/Users/spook/Documents/Home/01 Active/Personal/Summer2026RegistrarMonitor/registrar_monitor/assets/downloads`

Embedded worksheet classification:

| Embedded semester | Count | Earliest embedded timestamp | Latest embedded timestamp |
|---|---:|---|---|
| Fall 2025 | 497 | 2025-08-01 16:16:02 | 2025-12-18 23:10:22 |
| Spring 2026 | 494 | 2025-12-05 19:23:16 | 2026-01-16 14:06:05 |
| Summer 2025 | 3 | 2025-08-01 16:04:00 | 2025-12-18 23:10:56 |
| Summer 2026 | 3 | 2026-05-11 17:40:01 | 2026-05-11 17:42:26 |

The three Summer 2025 files have Fall/Spring-looking filename dates. Their
embedded semester is authoritative and explains apparent filename/DB anomalies.

### Legacy checkout

Path:
`/Users/spook/Documents/Index/40-49 Hobbies/41 Pet Programming/41.13 Registrar Monitor/assets/downloads`

| Embedded semester | Count | Earliest embedded timestamp | Latest embedded timestamp |
|---|---:|---|---|
| Fall 2025 | 497 | 2025-08-01 16:16:02 | 2025-12-18 23:10:22 |
| Spring 2026 | 497 | 2025-12-05 19:23:16 | 2026-01-31 22:36:19 |
| Summer 2025 | 3 | 2025-08-01 16:04:00 | 2025-12-18 23:10:56 |

### Summer 2025 raw archive

Path:
`/Users/spook/Downloads/assets/downloads/raw_excel`

All 70 files embed `Summer 2025`; all 70 hashes are distinct. Filename range:
`school_schedule_by_term_20250512_092443.xls` through
`school_schedule_by_term_20250526_005758.xls`.

### Loose registrar exports

All are under `/Users/spook/Downloads`.

| File | Embedded semester/scope | Embedded timestamp |
|---|---|---|
| `school_schedule_by_term (5).xls` | Fall 2023 | 2024-12-10 22:58:09 |
| `tutor.xls` | School of Engineering and Digital Sciences, Fall 2023 | 2024-07-26 22:32:15 |
| `tutor_ssh.xls` | School of Sciences and Humanities, Fall 2023 | 2024-07-26 23:41:52 |
| `school_schedule_by_term (3).xls` | Spring 2024 | 2024-12-10 00:21:43 |
| `school_schedule_by_term.xls` | Summer 2024 | 2024-07-28 23:45:13 |
| `school_schedule_by_term (2).xls` | Summer 2024 | 2024-12-10 00:17:55 |
| `school_schedule_by_term (1).xls` | Fall 2024 | 2024-12-10 00:17:44 |
| `school_schedule_by_term (30).xls` | Spring 2025 | 2024-12-09 15:19:15 |
| `school_schedule_by_term (31).xls` | Spring 2025 | 2024-12-09 16:19:47 |
| `school_schedule_by_term (4).xls` | Spring 2025 | 2024-12-10 09:01:28 |
| `school_schedule_by_term (6).xls` | Summer 2025 | 2025-04-09 10:15:40 |
| `school_schedule_by_term (7).xls` | Fall 2026 | 2026-07-14 19:51:43 |
| `school_schedule_by_term (8).xls` | Fall 2026 | 2026-07-17 21:06:43 |
| `school_schedule_by_term (9).xls` | SCAI, Fall 2026 | 2026-07-17 21:07:13 |
| `school_schedule_by_term (10).xls` | School of Engineering, Fall 2026 | 2026-07-17 21:07:15 |

Other `.xls` files in Downloads were inspected and excluded because their
worksheet structure/content is unrelated (budgets, territorial statistics,
email lists, and other non-registrar data). `ps.xls` is HTML with an `.xls`
extension and is also unrelated.

## Database catalog

### Canonical laptop checkout

| DB | Semester | Snapshots | Timestamp range | Role |
|---|---|---:|---|---|
| `data/enrollment_fall_2025.db` | Fall 2025 | 497 | 2025-08-01 16:16:02 — 2025-12-18 23:10:22 | Canonical |
| `data/enrollment_spring_2026.db` | Spring 2026 | 495 | 2025-12-05 19:23:16 — 2026-01-31 22:36:19 | Canonical |
| `data/enrollment_summer_2025.db` | Summer 2025 | 70 | 2025-05-12 09:24:43 — 2025-12-18 23:10:56 | Canonical but contains anomalous late snapshot |
| `data/enrollment_summer_2026.db` | Summer 2026 | 3 | 2026-05-11 17:40:01 — 2026-05-11 17:42:26 | Early local subset |
| `data/enrollment_spring_2025.db` | Spring 2025 | 254 | 2024-12-09 22:17:58 — 2025-01-10 20:45:10 | Historical |
| `data/enrollment_fall_2024.db` | Fall 2024 | 1 | 2024-01-01 12:00:00 | Seed/recovered |
| `data/enrollment_spring_2024.db` | — | 0 | — | Empty placeholder |
| `data/enrollment_fall_2026.db` | — | 0 | — | Empty placeholder |
| `data/enrollment.db` | — | 0 | — | Empty legacy default |

### Summer 2026 copies

| Path | Snapshots | Range | Relationship |
|---|---:|---|---|
| Runtime `data/enrollment_summer_2026.db` | 1,970 | 2026-05-11 17:40:01 — 2026-06-10 15:32:16 | Production source |
| Laptop `output/performance-input/enrollment_summer_2026-runtime.db` | 1,970 | Same | Full runtime-derived copy |
| Laptop archive `sources/laptop_databases/loose_root/enrollment_summer_2026_runtime_subset_through_2026-05-26.db` | 1,824 | through 2026-05-26 13:48:31 | Renamed former `summer_prod.db`; exact SHA-256 duplicate of `Downloads/enrollment_summer_2026 (1).db` |
| Laptop `Downloads/enrollment_summer_2026.db` | 600 | through 2026-05-13 17:15:00 | Earlier subset |

### Summer 2025 copies

| Path | Snapshots | Range | Relationship |
|---|---:|---|---|
| `data/enrollment_summer_2025.db` | 70 | through anomalous 2025-12-18 23:10:56 | Current canonical |
| Legacy checkout `data/enrollment_summer_2025.db` | 70 | Same | Exact SHA-256 duplicate of current recovered debug variant |
| `Downloads/Telegram Desktop/enrollment_summer_2025.db` | 69 | 2025-05-12 09:24:43 — 2025-05-26 00:57:59 | Clean historical subset matching the raw archive |
| Runtime `data/enrollment_summer_2025.db` | 0 | — | Empty placeholder |

### Recovered and generated copies

- `data/recovered_variants/` contains two 70-snapshot Summer 2025 variants,
  two four-snapshot Spring 2024 variants, and one one-snapshot Fall 2024
  variant.
- `assets/website/public/data/*.db` are empty generated placeholders and are
  not sources of truth.
- The legacy checkout contains historical copies for Fall 2024, Spring 2024,
  Spring 2025, Summer 2025, Fall 2025, and Spring 2026.
- Temporary `/private/tmp/registrar-*` databases are test/build artifacts and
  were excluded from archival matching.

## Runtime paths

- Raw spreadsheets:
  `/home/dmitry_s_ivanenko/registrar_monitor/assets/downloads`
- Databases:
  `/home/dmitry_s_ivanenko/registrar_monitor/data`

The runtime was inspected read-only through the account-derived OS Login
identity. Monitoring remained paused and no files or services were changed.
