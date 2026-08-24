# Test and operational tooling

The Makefile is the command index. Use `make help` for the current target list;
this document records contracts and artifact locations that are not obvious from a
command name.

## Quality layers

- `make check` is the standard local gate: Python formatting, linting, typing,
  coverage/tests, frontend unit tests, lint, and build. It does not run Playwright.
- `make site-smoke` builds and generates the dashboard, validates public output,
  and crawls generated same-origin HTML, assets, and JSON. Its structured result is
  `output/generated-site-crawl.json`.
- `make test-browser` exercises generated `assets/website/public` output with
  Playwright. Traces, screenshots, and HTML reports are under `output/playwright/`.
- `make worker-check` validates the isolated preview-image Worker, including its
  tests, types, and deployment dry-run.
- `make security` audits both JavaScript lockfiles at moderate severity or higher.
- `make release-candidate` assembles the broader stabilization release gate.

Generated-site CI seeds isolated, ignored SQLite fixtures for every configured
semester. Local test setup must not overwrite an existing database.

## Doctor

Use `make doctor` or `monitor doctor` locally. On a runtime host, run
`scripts/runtime_doctor.sh` as the application user; it uses locked dependencies
without syncing or requiring a writable uv cache.

The doctor checks toolchains and lockfiles, required paths with temporary write
probes, frontend executables, and SQLite integrity, foreign keys, and schema
versions. Missing optional state is a warning; integrity failures and missing
required tools are errors. It never prints `.env`. Use `monitor doctor --json` or
`--output` for machine-readable results.

Current host ownership, permissions, toolbox, and service procedures belong in
`production-topology.md`.

## Logs

Normal commands write human-readable stdout and rotating logs in the configured
logs directory. The scheduler also writes one JSON object per decision to
`scheduler_decisions.jsonl`. Keep logs free of secrets, environment dumps,
Telegram identifiers, and full registrar payloads.

For scheduler incidents, query a bounded time window from
`registrarmonitor.service`. For private-subscription incidents, use
`registrarmonitor-bot.service`. Never collect an unrestricted journal. Expected
no-ops are `INFO`, retryable degradation is `WARNING`, and failed operations are
`ERROR`. Workflow-boundary exceptions keep their tracebacks. Neither service may
log Telegram identifiers, tokens, or environment contents.

The subscription bot's focused gate is:

```bash
uv run pytest --no-cov \
  tests/test_subscription_store.py \
  tests/test_subscription_publication.py \
  tests/test_subscription_dispatcher.py \
  tests/test_subscription_catalog.py
```

The generated `registrarmonitor-bot.service` is a deployment artifact, not a
signal that production activation is authorized.

## Baselines and benchmarks

Routine reports go to ignored `output/`. Dated, intentionally recorded baselines
live under `docs/baselines/`; database copies remain ignored and are never uploaded.

Benchmark inputs must be disposable local SQLite copies. The runner hashes the
input before and after and performs writes only on disposable copies. Synthetic
benchmarks provide the non-secret CI/contributor path.

The suite separates readiness/infrastructure failures from performance-budget
failures and retains browser diagnostics under `output/benchmark-browser/`. It
measures full, no-change, and one-course generation; navigation and course-open
readiness; and publication file/byte shape. Remote Cloudflare latency is recorded
separately from deterministic CI budgets.

## CI and hooks

Python 3.13 is canonical; Python 3.14 is a compatibility job. Generated-site CI
uploads crawl and browser diagnostics, while benchmark CI uploads structured
results. Dependency review and lockfile audits enforce the repository security
policy.

Pre-commit stays fast: text hygiene, Ruff, Ty, and frontend lint. Run the broader
gates above when a change touches their behavior.
