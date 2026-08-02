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
`uv run --locked --no-sync --no-cache monitor doctor`, so it does not require
`make`, install dependencies, or require a writable uv cache. The doctor checks:

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

### VM shared toolbox and permissions

On `instance-20260501-152532`, the supported shared operator/runtime model uses
the `registrarmonitor` group for `spook` and `dmitry_s_ivanenko`. The checkout,
colocated `.git`/`.jj`, and `.venv` are operator-owned and group-writable; the
runtime data, logs, downloads, reports, generated public output, and maintenance
output are runtime-owned and group-writable. Setgid directories and default
ACLs preserve that access for newly created files. The project root is
traverse-only to the group, and `.env` remains `dmitry_s_ivanenko:dmitry_s_ivanenko`
mode `0600` with no shared ACL.

The VM toolbox contains only the requested Debian packages: `make`, `ripgrep`,
`jq`, `sqlite3`, `gh`, `acl`, `git`, `curl`, and `ca-certificates`. The existing
root-owned `/usr/local/bin/jj` is the verified official `v0.43.0` binary and was
retained (binary SHA-256
`7dfff2e4416e75e5ab20eaf741d60100f43be5a9b4d18c1347364e28a765edbe`). The
checkout's stale 19-path patch is archived under
`output/maintenance/vm-working-tree.patch` (SHA-256
`9c7040b7e2dacd17afd46cd898781b30f1edd8eb667654bf6c2e7d84135d23e6`) as
Jujutsu change `d65d29af` / local bookmark
`vm-pre-reconciliation-2026-08-02`; the active working copy is clean on
`main`/`6a25c7c4`.

For the repaired host, run the runtime diagnostic as `dmitry_s_ivanenko` and
run the operator gate with a raised file-descriptor limit while keeping dotenv
disabled so the operator never needs `.env` access:

```bash
sudo -u dmitry_s_ivanenko -H \
  /home/dmitry_s_ivanenko/registrar_monitor/scripts/runtime_doctor.sh

sudo -u spook -H prlimit --nofile=65536:65536 -- \
  env PYTHON_DOTENV_DISABLED=1 \
  make -C /home/dmitry_s_ivanenko/registrar_monitor check-fast
```

The 2026-08-02 verification returned 22 doctor passes, 7 informational
warnings, 0 failures, and `748 passed` in `make check-fast`; monitoring stayed
paused throughout.

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
inventory, initial browser transfer/request counts, validated-summary bytes,
mark-derived grid rendering, course-card readiness, and opening one course.
Website generation runs through `WebsiteService` and reports the legacy root
payload versus the new summary bytes. Focused `benchmark-database`, `benchmark-website`, and
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
