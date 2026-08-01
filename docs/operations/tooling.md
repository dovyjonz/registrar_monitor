# Test and operational tooling

The Makefile is the command index. The commands below deliberately separate
fast, deterministic checks from generated-site and browser work.

## Frontend tests

`npm --prefix assets/website run test:unit` runs pure helper and Vite
configuration tests with Node's built-in `node:test` runner. Keep pure modules
such as URL slug and chart mapping helpers here; they do not need a browser.
`make check` includes these unit tests.

`npm --prefix assets/website run test:e2e` runs Playwright Chromium smoke tests.
The Playwright web server serves `assets/website/public` with Python's static
server, so the suite exercises generated production output rather than Vite's
development shell. `make test-browser` builds assets, generates the site,
installs Chromium if needed, and runs the suite. Failure screenshots, traces,
and the HTML report are written under `output/playwright/`.
For an already-installed Chromium-compatible browser, set
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to its executable path; CI uses the pinned
Playwright Chromium download.

## Generated-output integrity

`make site-smoke` builds and generates the dashboard, then crawls every generated
HTML page. It combines `WebsiteService.validate_public_output()` with checks for
missing same-origin links, assets, and JSON payloads. The structured result is
written to `output/generated-site-crawl.json`.

CI seeds one small deterministic SQLite fixture for each configured semester
before this job. The fixtures exist only in ignored runtime output and are never
used by production.

## Operational doctor

Run `make doctor` or `monitor doctor` during local development. On a runtime
host, run `scripts/runtime_doctor.sh` as the application runtime user; it calls
`uv run --locked --no-sync monitor doctor`, so it does not require `make` and
does not install dependencies. The doctor checks:

- Python, uv, Jujutsu, Node.js, npm, and the pinned Node version;
- required TOML and lockfiles, plus optional `.env` presence without reading or
  printing secrets;
- configured data, download, report, log, and generated-site paths with a
  temporary write probe;
- installed Vite and Playwright executables;
- every enrollment SQLite database with `PRAGMA integrity_check`,
  `PRAGMA foreign_key_check`, and `PRAGMA user_version`.

Warnings describe optional or migratable state, such as a missing `.env`, no
databases, or a legacy schema-version marker. Integrity failures, missing
required tools, and missing frontend prerequisites return a non-zero exit code.
Use `monitor doctor --json` for stdout JSON or
`monitor doctor --output output/doctor.json` to save it.

New databases set SQLite `user_version` to the application's
`EXPECTED_SCHEMA_VERSION`. The diagnostic is read-only and does not migrate a
database.

## Baselines and benchmarks

`make baseline` writes `output/tooling-baseline.json`. It records platform and
toolchain information, lockfile hashes, and the complete doctor report. It
contains no environment values, tokens, chat identifiers, or database content.

`make benchmark DATABASE=output/performance-input/<copy>.db` writes
`output/performance-baseline.json`. The input must be a local, ignored SQLite
copy: the runner hashes it before and after and performs writes only against
disposable copies. `make benchmark-synthetic` provides a deterministic,
non-secret fallback for contributors without runtime access.

The suite separates cold and warm samples and records raw values, median, and
nearest-rank p95. It covers SQLite allocation and poll deltas, latest-state and
course-history reads, SQL activity and website generation, generated file
inventory, initial browser transfer, course-card readiness, and opening one
course. Focused `benchmark-database`, `benchmark-website`, and
`benchmark-browser` targets accept the same `DATABASE`, `PERF_COLD`, and
`PERF_WARM` inputs.

`make benchmark-record` is the explicit command for dated Markdown and JSON
under `docs/baselines/`. `make benchmark-record-deploy` additionally creates one
Cloudflare Pages preview-branch deployment and records its duration and payload
size. It requires existing Cloudflare credentials and never targets the
production branch.

Routine reports are machine-readable JSON under the ignored `output/`
directory. Dated baseline JSON is intentionally committed beside its Markdown
rendering; copied databases are never committed or uploaded as CI artifacts.

## CI and dependency review

Python 3.13 remains the canonical format, lint, type, coverage, and test job.
Python 3.14 runs a separate compatibility test job. JUnit and coverage XML are
uploaded from both jobs.

The generated-site job runs the crawler and Playwright, then uploads crawl,
trace, screenshot, and HTML reports. A separate benchmark job uploads its JSON
result.

On pull requests, GitHub's dependency review blocks newly introduced
moderate-or-higher known vulnerabilities and `npm audit` checks the complete
frontend lockfile. Dependabot proposes grouped weekly Python and npm updates and
grouped monthly GitHub Actions updates, keeping each pull request reviewable.

## Local hooks

Ty is the sole repository-configured Python type checker. Pre-commit remains
limited to text hygiene, Ruff, Ty, and frontend lint. It does
not install browsers, generate the website, crawl output, run benchmarks, or run
the full test suite. Use `make check`, `make site-smoke`, and
`make test-browser` before sharing cross-cutting tooling changes.
