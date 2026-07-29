# Reproducible baseline

This baseline was refreshed on **2026-07-28** in `Asia/Almaty`. It records the
local repository and toolchain, verification results, repository size, supported
operational paths, and production evidence available at that time.

Production findings are summarized here for context. The detailed evidence,
classification rules, safe verification commands, and evidence boundaries are in
[production-topology.md](production-topology.md).

## Repository state

Registrar Monitor is a colocated Jujutsu/Git repository.

| Item | Baseline value |
|---|---|
| Working-copy change ID | `ozrnnzspxwsx` |
| Working-copy commit before this document was added | `88f87b2de34a` |
| Parent/content commit | `548ad184cdfc` — `Modernize tooling and prepare Fall 2026 monitoring` |
| Worktree before this document was added | Modified `AGENTS.md`; added `docs/operations/production-topology.md` |
| Ownership of pre-existing changes | Operator/user-owned; preserved |

Jujutsu commit IDs evolve whenever working-copy content changes. The change ID is
the stable identifier for the current line of work; the recorded working-copy
commit ID identifies the pre-document snapshot.

## Toolchain

| Tool | Version |
|---|---|
| Project Python | 3.13.5 |
| System `python3` | 3.14.5 |
| Node.js | 26.5.0 |
| npm | 11.17.0 |
| uv | 0.11.32 |
| Jujutsu | 0.43.0 |
| Vite used by website build | 8.1.5 |

`pyproject.toml` requires Python 3.13 or newer. The project virtual environment
uses Python 3.13.5 even though the system interpreter is newer.

## Verification results

### `make check`

**Result: passed, exit 0.**

| Stage | Result |
|---|---|
| Ruff format check | Passed; 97 files already formatted |
| Ruff lint | Passed |
| `ty check` | Passed with no diagnostics |
| Pytest | 612 passed; 2 warnings |
| Coverage | 77.82%; required minimum 75% |
| Website ESLint | Passed |
| Website build | Passed |

The Python test run used Python 3.13.5, pytest 9.1.1, and coverage 7.1.0.

### Website tests

`npm --prefix assets/website test` passed:

- 6 tests passed;
- 0 failed, skipped, cancelled, or marked todo;
- observed duration: 96 ms.

### Standalone website build

`npm --prefix assets/website run build` passed:

- Vite 8.1.5;
- 15 modules transformed;
- observed build time: 102 ms;
- largest emitted asset: `chart-3ZcqxwdW.js`, 164.65 kB and 54.80 kB gzip.

The generated asset hashes are build outputs and may change with toolchain or
source changes.

## Repository and generated-output sizes

Sizes are allocated filesystem sizes reported by `du -sh`.

| Path | Size |
|---|---:|
| Entire repository | 657 MiB |
| `assets/website/node_modules` | 288 MiB |
| `.venv` | 164 MiB |
| `data` | 97 MiB |
| `.git` | 69 MiB |
| `htmlcov` | 3.0 MiB |
| `assets/website/public` | 2.9 MiB |
| `.ruff_cache` | 1.0 MiB |
| `.jj` | 800 KiB |
| `assets/downloads` | 360 KiB |
| `.pytest_cache` | 76 KiB |
| `logs` | 64 KiB |
| `assets/changes` | 4 KiB |

Runtime and generated output are intentionally gitignored. Size changes in those
paths do not imply tracked source changes.

## Operational-path evidence

The status terms below distinguish repository support from production activity:

- **Supported** means the path is wired into current source, documentation, or
  tests.
- **Observed production path** means production command output or logs directly
  establish its use.
- **Inactive** means the 2026-07-28 production inspection established it was not
  running.
- **Unknown** means the available evidence does not establish the claim.

### `monitor schedule`

- **Entry point:** the `monitor` console script dispatches `schedule` through
  `registrarmonitor.main`, `ScheduleCommand`, and `TwoPhaseScheduler`.
- **Configuration:** `settings.toml` supplies semester milestones and deadlines,
  registrar source URL, directories, website update interval, and Pages project.
- **Status:** **supported and observed in production, but inactive at inspection**.
  Both installed production systemd units invoked `uv run monitor schedule`; both
  were failed/inactive. Polling, reporting, and deployment have been stale since
  2026-06-10.
- **Secrets:** Telegram token/chat ID when Telegram is enabled; Cloudflare API
  token/account ID for scheduler-triggered Pages deployment.
- **Runtime state:** registrar network access, SQLite semester databases,
  reporting-log rows, raw downloads, scheduler decisions, application logs,
  generated website output, Node dependencies, and host-local time.

### Cron setup

- **Entry point:** `scripts/setup_cron.sh` installs
  `monitor report --stateful` at minutes 15 and 45.
- **Configuration:** project path, the user's crontab, application settings, and
  project-local `.env`.
- **Status:** **supported installer; inactive in production at inspection**. The
  cron daemon was active, but no active Registrar Monitor cron job was found.
- **Secrets:** Telegram credentials if reports are sent.
- **Runtime state:** an already-populated SQLite database, reporting-log state,
  `uv`, and writable cron report logs. This path does not poll for new snapshots.

### systemd setup

- **Entry point:** `scripts/setup_vps.sh` generates
  `registrarmonitor.service` with `ExecStart=/usr/bin/env uv run monitor schedule`.
- **Configuration:** generated unit file, project root, runtime user, `.env`,
  restart policy, and systemd enablement.
- **Status:** **supported and observed in production, but inactive**. Production
  had two units at the 2026-07-28 inspection. `registrarmonitor.service` is now
  canonical and remains disabled/failed; the obsolete `registrar-monitor.service`
  was disabled on 2026-07-29. Monitoring remains intentionally paused.
- **Activation prerequisite:** complete the planned data changes and have an
  operator explicitly verify them. Activation is a separate manual action;
  `scripts/setup_vps.sh` does not enable or start the service.
- **Secrets and runtime state:** the complete scheduler requirements above plus a
  Linux/systemd host and readable project-local `.env`.

### `monitor run`

- **Entry point:** `RunCommand` performs poll, report, and website generation;
  `--deploy` additionally deploys the generated website.
- **Configuration:** registrar URL, directories, active-semester settings,
  Telegram settings, website settings, and optional Pages target.
- **Status:** **supported; production use unknown**. Tests cover the complete
  workflow, but production evidence identifies the long-running scheduler rather
  than `monitor run`.
- **Secrets:** Telegram credentials unless `--no-telegram`; Cloudflare credentials
  only with `--deploy`.
- **Runtime state:** registrar access, SQLite databases, downloads, reports,
  generated website output, and Node dependencies.

### Cloudflare Pages deployment

- **Entry point:** `WebsiteService.deploy()` runs
  `npx wrangler pages deploy public --project-name <project>`. It is reachable
  through `monitor deploy --deploy`, `monitor run --deploy`, and scheduler website
  updates.
- **Configuration:** `[website]` in `settings.toml`, CLI project/branch overrides,
  Wrangler dependencies, and generated `assets/website/public`.
- **Status:** **supported and observed production deployment path**. Production
  journal and Cloudflare evidence show a successful direct Pages upload on
  2026-06-10 to project `registrar-monitor`, production branch `main`. No Git
  integration was reported.
- **Secrets:** `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
- **Runtime state:** validated generated public assets, Node/npm, Wrangler, and
  Cloudflare network access.

### Retired Cloudflare Worker asset deployment

- **Status:** **retired**. The unobserved `wrangler deploy --assets` script and
  Worker entry point were removed. Direct Cloudflare Pages upload through
  `WebsiteService.deploy()` is the only supported deployment path.

## Confirmed production findings

The 2026-07-28 read-only production inspection established:

1. no Registrar Monitor process was running;
2. no active Registrar Monitor cron job was installed;
3. both installed scheduler systemd units were inactive and failed;
4. the last successful poll was 2026-06-10 15:32:18 +05;
5. the last successful report was 2026-06-10 15:32:23 +05;
6. the last successful Pages deployment was 2026-06-10 15:33:17 +05;
7. production uses direct Cloudflare Pages upload; the unverified Worker path
   has been retired;
8. the VPS checkout was revision `668893f`, older than this local repository;
9. no local Registrar Monitor backup job or backup files were found in the
   inspected bounded locations;
10. off-host Google Cloud backup coverage remains unknown.

## Operator confirmations still required

1. Should cron reporting exist as an independent fallback?
2. Should the Worker deployment command be retained or retired?
3. When and how should the stale VPS checkout be updated?
4. Who owns database backups, retention, RPO/RTO, encryption, and restore tests?
5. Are Google Cloud disk snapshots or another off-host backup mechanism active?
6. Has the credential-like value found in commented production crontab material
   been rotated?
7. What alert should fire when polling, reporting, deployment, or backup evidence
   becomes stale?

## Reproduction commands

Run from the repository root. These commands do not print secret values.

### Repository state and versions

```bash
jj status
jj log --no-graph \
  -r 'present(@) | ancestors(@, 5) | present(trunk())'
jj diff --stat

python3 --version
UV_CACHE_DIR=/private/tmp/uv-cache uv run --no-sync python --version
node --version
npm --version
uv --version
jj --version
```

### Verification

The test, coverage, and build commands write only expected gitignored outputs
such as `.coverage`, `htmlcov`, tool caches, and generated website assets.

```bash
UV_CACHE_DIR=/private/tmp/uv-cache make check
npm --prefix assets/website test
npm --prefix assets/website run build
```

### Sizes

```bash
du -sh \
  . \
  data \
  logs \
  assets/downloads \
  assets/changes \
  assets/website/public \
  assets/website/node_modules \
  .venv \
  .git \
  .jj \
  htmlcov \
  .pytest_cache \
  .ruff_cache
```

### Production

Use the bounded, read-only checklist in
[production-topology.md](production-topology.md#safe-read-only-verification-checklist).
Do not print `.env`, process environments, Telegram identifiers, Cloudflare
tokens, unrestricted crontabs, or unrestricted runtime configuration.

## Evidence boundary

- Local verification was run against the working copy identified above.
- Existing `AGENTS.md` and `production-topology.md` changes were preserved.
- Production facts are observations from 2026-07-28 and may become stale.
- No secret value or Telegram identifier is recorded in this baseline.
- Passing local tests does not establish that production is running or current.
