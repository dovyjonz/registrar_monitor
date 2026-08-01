# Reproducible baseline

This document defines what must be reproducible and records the latest full
verification. It deliberately excludes working-copy IDs, cache sizes, generated
asset hashes, and exhaustive command output: those values change frequently and
are directly discoverable from the repository or generated artifacts.

Production state is maintained separately in
[production-topology.md](production-topology.md).

## Reproducibility contract

| Concern | Authoritative source |
|---|---|
| Python version | `.python-version` and `pyproject.toml` |
| Python dependencies | `uv.lock` |
| Node.js version | `.node-version` |
| Website dependencies | `assets/website/package-lock.json` |
| Setup and checks | `Makefile` and `scripts/runtime_doctor.sh` |
| Non-secret runtime configuration | `settings.toml` |
| Secret names and examples | `.env.example` |
| Database schema and migrations | application source and `DATABASE.md` |
| Production topology | `docs/operations/production-topology.md` |

`make bootstrap` installs lockfile-pinned Python and website dependencies.
`make doctor` validates the local toolchain and important paths without requiring
optional secrets. `make` is not a production runtime requirement:
`scripts/runtime_doctor.sh` runs the same doctor through the installed `uv`
environment with `--locked --no-sync`, so an SSH operator can verify the
runtime without installing `make` or changing dependencies.

Makefile commands also construct a non-login runtime `PATH`: an exact Node
installation under `$HOME/.local/share/registrar-monitor/node-v<version>/bin`
or the matching nvm directory takes precedence, followed by user-local `uv`,
Cargo-installed Jujutsu, and standard Homebrew locations. This keeps the
`.node-version` selection and user-installed CLI tools stable when a caller
does not load interactive shell startup files. The tools still need to be
installed on the host; `make doctor` reports a missing installation.

The frontend has `engine-strict=true`, so an unsupported Node or npm version
fails installation instead of leaving a misleading engine warning.
`make baseline` writes a structured JSON report containing input hashes and the
doctor results. See [tooling.md](tooling.md) for field and command details.

## Verification levels

Choose the smallest level proportional to the change:

| Level | Command | Intended use |
|---|---|---|
| Focused | nearest pytest file, Ruff path, or website test | local iteration |
| Python | `make check-fast` | Python-only changes |
| Full | `make check` | cross-cutting, scaffold, or handoff verification |
| Browser | `make test-browser` | dashboard behavior |
| Deployable site | `make site-smoke` | generated output and link integrity |

Generated output and tool caches may change during verification but remain
gitignored. `make clean-generated` removes only reproducible website output; it
must not remove databases, downloads, reports, or logs.

## Latest full verification

The most recent full quality gate was run on 2026-07-29 in `Asia/Almaty`:

- `make check`: passed
- Ruff formatting and lint: passed
- `ty check`: passed
- pytest: 652 passed with 32 warnings
- coverage: 78.39%, above the 75% threshold
- website ESLint and Vite build: passed

The pure JavaScript `node:test` suite and generated-site Playwright smoke were
last recorded on 2026-07-29 with 9 and 1 passing tests, respectively. A focused
Python 3.14 compatibility run passed 43 tests.

These results describe that revision only. Run the relevant verification again
after source, dependency, or toolchain changes.

## Supported operational paths

| Path | Repository status | Production status |
|---|---|---|
| `monitor schedule` via `registrarmonitor.service` | Canonical; includes stateful reports at :15 and :45 | Installed but paused |
| External Registrar Monitor cron | Retired | No active entries |
| `monitor run` | Supported operator workflow | Production use not established |
| Direct Cloudflare Pages upload | Canonical website deployment | Last observed success 2026-06-10 |
| Worker asset deployment | Retired | Not present |

The obsolete `registrar-monitor.service` unit was removed from production on
2026-07-29. Only `registrarmonitor.service` is supported.

## Runtime dependencies

The scheduler needs:

- registrar network access;
- writable per-semester SQLite databases;
- the project-local `.env` for enabled Telegram or Cloudflare features;
- Node.js and locked website dependencies for dashboard generation;
- a Linux/systemd host when run as the production daemon.

The daemon's twice-hourly stateful reporter reads existing SQLite snapshots and
writes reporting state; it does not force an additional poll.

## Evidence boundaries

- Passing local checks does not prove that production is active or current.
- Production monitoring is intentionally paused; activation requires a separate,
  explicit operator decision.
- Never include `.env`, process environments, Telegram identifiers, Cloudflare
  tokens, unrestricted crontabs, or unrestricted service configuration in a
  baseline.
- Use the bounded checks in `production-topology.md` before making an operational
  decision.
