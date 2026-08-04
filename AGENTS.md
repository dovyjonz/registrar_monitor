# Agent guide

## Start here

Registrar Monitor is a Python 3.13 application that downloads registrar
spreadsheets, stores normalized enrollment snapshots in SQLite, reports changes,
and publishes a static dashboard to Cloudflare Pages.

Use these sources in this order:

1. `AGENTS.md` for repository constraints and safe working practices.
2. `settings.toml` for semester dates, milestones, timezone, paths, and website
   settings.
3. `docs/operations/production-topology.md` for current runtime state and the
   production verification runbook.
4. `README.md` and `DATABASE.md` for operator usage and storage details.
5. `docs/adr/` and `CONTEXT.md`, when present, for recorded domain decisions.

Do not copy command inventories into this file. The Makefile and
`monitor --help` are the authoritative, easily discoverable command references.

## Current production state

As verified on 2026-08-04:

- Google Cloud project `registrarmonitor` contains the runtime VM
  `instance-20260501-152532` in `us-east1-c`.
- Monitoring is active. The scheduler process is running under the sole supported
  `registrarmonitor.service` unit.
- `registrarmonitor.service` is installed, enabled, and active/running.
- The VM checkout is clean on `main`/`origin/main` at `c48e35fb`; the canonical
  service was restarted after synchronization and remains active/running.
- The latest forced Pages deployment is
  `019c6b0e.registrar-monitor.pages.dev`; the canonical domain returned HTTP
  200 after deployment.
- The obsolete `registrar-monitor.service` unit was removed from the host.
- The cron daemon is active, but no active Registrar Monitor entry exists in the
  runtime-user, root, operator, or system crontabs.

Do not change the monitoring service state unless the operator explicitly asks
and the planned data changes have been verified. Repository setup, code sync,
and reporting tests are separate from service-state changes.

## Repository invariants

- SQLite is the source of truth for enrollment snapshots. Do not add JSON
  snapshot persistence to the normal monitor path.
- `settings.toml` is the single source of truth for semester milestones,
  deadlines, and the registrar timezone. Scheduler and website behavior derive
  from it.
- Secrets belong in `.env`. Never commit, print, or copy tokens, chat IDs,
  process environments, or unrestricted runtime configuration.
- Generated and runtime output stays untracked: `data/`, `logs/`,
  `assets/downloads/`, `assets/changes/`, `assets/website/public/`, coverage
  output, and local caches.
- Direct Cloudflare Pages upload is the supported website deployment path.
  Do not reintroduce the retired Worker asset-deployment path.
- Keep generated website pages and payloads out of source edits unless the task
  explicitly concerns generated artifacts.

## Code layout

The Python package is under `src/registrarmonitor`; the Vite dashboard is under
`assets/website`.

Prefer existing boundaries:

- `automation/`: downloading and scheduler behavior
- `data/`: parsing, SQLite, migrations, and comparisons
- `reporting/`: text, Telegram, and optional PDF output
- `services/`: workflow orchestration
- `website/`: static page and payload generation
- `models.py`: enrollment domain models

Put reusable pytest fixtures in `tests/conftest.py`.

## Setup and verification

Use the pinned toolchains and lockfiles:

```bash
make bootstrap
make doctor
```

For a narrow change, run the closest relevant test or check. For Python-only
iteration use `make check-fast`. Before handing off scaffold or cross-cutting
changes, run:

```bash
make check
```

Use `UV_CACHE_DIR=/private/tmp/uv-cache` if the default uv cache is unavailable
in a sandbox.

### Generated dashboard debugging

Serve generated output, not Vite's asset base:

```bash
cd assets/website/public
python3 -m http.server 8000 --bind 127.0.0.1
```

If generated HTML references stale asset hashes after a website build, compare it
with `assets/website/public/assets/.vite/manifest.json`, then run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run python -c "from registrarmonitor.services.website_service import WebsiteService; WebsiteService()._patch_asset_hashes_in_html()"
```

Hard-refresh the browser after patching.

## Common pitfalls and papercuts

- Jujutsu commands snapshot `@`, and concurrent operations can commit or
  restore working-copy content unexpectedly. Inspect `jj op log` before
  recovery, then re-check `jj status`, a bounded `jj log`, and `jj diff` after
  every restore, commit, or bookmark move. A clean empty `@` does not prove
  that the intended files are present.
- `jj commit` does not move bookmarks. For a deliberate `main` commit, commit
  the working copy, move `main` to the new commit, fetch before pushing, use a
  bookmark-specific dry-run, and confirm `main@origin` afterward.
- The production VM is a colocated Jujutsu/Git checkout whose system Git is
  too old for the native `jj git fetch` path. Use the documented system-Git
  fetch followed by Jujutsu import and bookmark/working-copy reconciliation.
  The `.jj/working_copy` metadata is owned by the SSH checkout owner, so run
  Jujutsu synchronization as that owner; separately verify that the runtime
  user can read the updated source files.
- `gcloud compute ssh` needs exact leaf-level help validation, explicit
  project/zone scoping, `--quiet`, and a `--dry-run` preview. The local
  sandbox may block the gcloud credentials database; retry the same approved
  read-only command with elevated permission instead of changing repository or
  VM state to work around it.
- Chromium smoke tests can fail before test startup in the sandbox with a
  macOS Mach-port permission error. Treat that as an environment failure and
  rerun the same browser check with the approved elevated execution path.
- A Vite build can leave generated HTML pointing at an older asset hash. Use
  the generated `public` output, compare `.vite/manifest.json`, and run the
  supported forced `monitor deploy --force --deploy` path when the deploy must
  include current assets. Validate the deployed hashed asset, not only the
  source build log.
- Forced deployment can be quiet while generating the course-share pages and
  uploading thousands of files. Poll the existing process/session; do not
  start a second deployment while the first is still running.
- Updating the VM checkout does not reload an already-running Python process.
  After an explicitly authorized code sync, restart only
  `registrarmonitor.service` and verify its active/running state, new process
  start time/PID, and source checksums. Never revive the retired unit.
- Chart.js layering is easy to regress: regular point and hover radii must be
  zero when dots are removed, while capacity-change markers remain visible;
  horizontal guides need `drawTime: 'beforeDatasetsDraw'` and `z: -1` so they
  stay behind the yellow course series and markers.

## Version control

This is a colocated Jujutsu/Git repository. Use Jujutsu for normal inspection and
changes:

1. Start with `jj status`, a bounded `jj log`, and `jj diff`.
2. Treat `@` as the working-copy commit and use change IDs for evolving work.
3. Preserve unrelated working-copy changes.
4. Fetch and inspect bookmarks before sharing; preview pushes with
   `jj git push --dry-run`.
5. Inspect `jj op log` before recovery. Prefer `jj undo` or a targeted operation
   restore/revert over Git reset commands.

Do not use Git commands that rewrite or clean the worktree or history unless
Git-specific behavior is explicitly required and the Jujutsu consequences have
been inspected. Do not add `Co-Authored-By` trailers.

## Production operations

Use `gcloud` for the Google Cloud runtime and follow the authoritative
`google/skills@gcloud` skill:

- validate every exact leaf command with `gcloud help`;
- specify project and zone explicitly;
- bound list output with filters, limits, or projections;
- use read-only inspection before mutation;
- preview `gcloud compute ssh` with `--dry-run`;
- never expose credentials, Telegram identifiers, environment contents, or full
  crontabs.

Require explicit operator authorization for destructive, IAM, billing,
organization, KMS, API-enabling, service-activation, or other materially
state-changing operations. The safe runtime checks and current resource
identifiers are maintained in
`docs/operations/production-topology.md`.

## Agent workflows

- Local issue files: `docs/agents/issue-tracker.md`
- Canonical triage roles: `docs/agents/triage-labels.md`
- Domain-document conventions: `docs/agents/domain.md`
