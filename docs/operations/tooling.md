# Test and operational tooling

The Makefile is the command index. Use `make help` for the current target list;
this document records contracts and artifact locations that are not obvious from a
command name.

## Quality layers

- `make check-fast` runs Ruff format check, Ruff lint, ty, and pytest.
- `make check` adds frontend lint, Node unit tests, and the Vite build. It does
  not run Playwright.
- `make test-browser` generates a deterministic fixture site and runs the Chromium
  smoke tests in `assets/website/test/browser-smoke.spec.mjs` against it. Traces,
  screenshots, and HTML reports are under `output/playwright/`.
- `make worker-check` validates the isolated preview-image Worker, including its
  tests, types, and deployment dry-run.
- `make site-generate` builds the dashboard and generates the production site.

CI runs three jobs: Python 3.13 (Ruff, ty, pytest), Frontend and Worker (lint, unit
tests, build, Worker checks), and Browser smoke, which runs only when files under
`assets/website/` or `src/registrarmonitor/website/` change. Coverage is optional:
`uv run pytest --cov=src/registrarmonitor`.

The browser fixture seeds isolated, ignored SQLite databases for every configured
semester. Local test setup must not overwrite an existing database.

## Doctor

Use `monitor doctor` locally. On a runtime host, run
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
uv run pytest \
  tests/test_subscription_store.py \
  tests/test_subscription_publication.py \
  tests/test_subscription_dispatcher.py \
  tests/test_subscription_catalog.py
```

The generated `registrarmonitor-bot.service` is a deployment artifact, not a
signal that production activation is authorized.

## CI and hooks

Dependabot runs monthly and groups updates per ecosystem.

Dependabot uses the `uv` ecosystem so Python updates include `uv.lock`, following
[uv's integration guidance](https://docs.astral.sh/uv/guides/integration/dependabot/).
The lockfile includes AnyIO 4.14.2, which fixes GHSA-82r6-8w77-94w6. Runtime
lockfile changes are verified by the Python job; synchronizing the VM remains a
separate production action.

Pass Playwright options after npm's `--` separator so npm does not consume
`--with-deps`. The browser job installs Chromium and its system libraries through
that option.

Wrangler is pinned to 4.147.0 in both JavaScript packages. The `sharp` 0.35.5
override addresses GHSA-wq5f-xc86-pv6w; the Worker also pins transitive `undici`
7.29.1 to address the advisories affecting 7.29.0. Remove these overrides when
the upstream Miniflare and Worker test-pool pins include the patched versions.
`make worker-check` verifies the overridden
dependencies in the actual Worker test runtime and deployment dry-run.

The Cloudflare Worker test pool requires Vitest 4. Keep major Vitest updates
separate until the pool's peer dependencies support them; Dependabot ignores
automatic major updates for that package.

Pre-commit runs Ruff (format and lint) and the private-key check; CI runs the rest.
