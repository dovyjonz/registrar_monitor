# Stabilization verification record — 2026-08-20

This dated record maps the 19 original stabilization reports to durable
regression evidence. Current behavior is defined by code, tests, configuration,
ADRs, and operational contracts rather than the completed implementation plan.

Follow-up review on 2026-08-22 resolved the remaining report, priority, preview,
and route-contract findings. Telegram now keeps headings proportional, renders
only aligned detail rows as monospace, and splits only between complete course
blocks. Before registration opens, the first gate is future rather than active.

## Issue traceability

| # | Original report | Primary regression or verification artifact |
|---:|---|---|
| 1 | Dragging/swiping can close the modal | `chart drag ending on the backdrop does not dismiss or activate the page`; touch-drag and pinch regressions; 10-run stability gate |
| 2 | Course-code jump goes too far down | `generated production site serves a working semester dashboard`, including the measured department-heading viewport position |
| 3 | Instructor button remains stuck without prior history | `professor no-history state returns to the current course view without reload` |
| 4 | Equal elapsed intervals render unequally in `Time` | `timeline mapping preserves literal elapsed durations across long gaps`; mobile time/cursor browser regression |
| 5 | Year-priority labels are inconsistent | Shared Python/browser compact and full forms; pre-opening future-gate regression |
| 6 | Telegram reports repeat routine information | Complete rendered-message contract; Markdown-aware course-block splitting regressions |
| 7 | Preview card and metadata repeat the same facts | `tests/test_website_preview.py`; preview Worker card/model tests |
| 8 | Copy uses too many colons | Shared copy tests enforce short separators, Unicode minus, and compact priority labels |
| 9 | CI/browser checks are flaky | retries disabled in Playwright configuration; deterministic readiness diagnostics; 10 first-attempt stability runs, 60/60 passed |
| 10 | Regression coverage is too low | 80% enforced floor in `pyproject.toml`; full suite reached 80.80% |
| 11 | Mobile button/dropdown text is oversized | focused WebKit regression verifies native-select font size and 44 px touch target |
| 12 | Generation/deployment is slow | `tests/test_performance_benchmark.py`; enforced synthetic timing/publication budgets; dated checkpointed-runtime baseline |
| 13 | Mobile priority tooltip is clipped or misaligned | `narrow full-capacity graph states keep the readout stable and unclipped`; mobile layout browser regression |
| 14 | Required-type-full periods are not explicit | Web/chart/preview evidence; Telegram names a limiting type only for an actual type imbalance, otherwise `100%` |
| 15 | Red course buttons turn black in Safari | focused WebKit resting/focus regression for semantic full-course styling |
| 16 | Semester/hash behavior looks strange or stale | producer/Worker token tests; explicit-share browser regression; live/archived route checks; mutable/immutable cache-header tests |
| 17 | Course modal omits semester context | template assertions and generated-site modal identity/accessibility regression |
| 18 | Past-semester data extends beyond its observation period | archived-window pipeline/preview tests; historical alignment and endpoint unit tests; observation-vs-removal browser regressions |
| 19 | Dependency/security alerts remain | both lockfile audits pass at moderate severity or higher; Worker typecheck and dry-run pass |

## Integrated evidence

All 14 implementation tickets and 19 original reports are mapped. On 2026-08-22,
`make check` passed 798 Python and 67 frontend tests, `make worker-check` passed
23 Worker tests plus typecheck and deploy dry-run, and isolated generated-site
Chromium verification passed 24/24 tests. Earlier WebKit, stability, security,
crawl, and performance evidence remains recorded in the dated baselines.

Durable numeric evidence is in `docs/baselines/performance-2026-08-20.{json,md}`
and `docs/baselines/stabilization-publication.json`. The integrated commands live
in the Makefile and CI workflow. This verification did not push code, deploy,
synchronize the VM, or change the monitoring service.
