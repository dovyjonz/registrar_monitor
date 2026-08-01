.DEFAULT_GOAL := help

# Make is often launched by a non-login shell (for example, an editor task or
# a service wrapper). Prefer the project-pinned Node installation and include
# the standard user-local locations used by uv and Jujutsu before inheriting
# the caller's PATH.
NODE_VERSION := $(shell tr -d '[:space:]' < .node-version)
RUNTIME_PATHS := $(HOME)/.local/share/registrar-monitor/node-v$(NODE_VERSION)/bin:$(HOME)/.nvm/versions/node/v$(NODE_VERSION)/bin:$(HOME)/.local/bin:$(HOME)/.cargo/bin:/opt/homebrew/opt/node@24/bin:/usr/local/opt/node@24/bin:/opt/homebrew/bin:/usr/local/bin
INHERITED_PATH := $(or $(PATH),/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin)
PATH := $(RUNTIME_PATHS):$(INHERITED_PATH)
export PATH
RUNTIME_ENV = PATH="$(PATH)"
UV = $(RUNTIME_ENV) uv
NPM = $(RUNTIME_ENV) npm

.PHONY: help bootstrap doctor sync format format-check lint type test website-install website-lint website-test-unit website-build check-fast check site-generate test-browser site-smoke baseline benchmark benchmark-database benchmark-website benchmark-browser benchmark-synthetic benchmark-record benchmark-record-deploy prototype-checkpointed-state prototype-checkpointed-state-targeted clean-generated

PERF_COLD ?= 10
PERF_WARM ?= 20
PERF_OUTPUT ?= output/performance-baseline.json
PROTOTYPE_OUTPUT ?= output/checkpointed-state-prototype.json
PROTOTYPE_MARKDOWN ?= output/checkpointed-state-prototype.md
PROTOTYPE_TARGETED_SAMPLES ?= 100

help:
	@printf '%s\n' \
		'Available targets:' \
		'  bootstrap       Install the pinned Python and Node dependencies' \
		'  doctor          Verify required tools, versions, config, paths, and secret files' \
		'  sync            Install Python development dependencies with uv' \
		'  format          Format Python code with Ruff' \
		'  format-check    Check Python formatting' \
		'  lint            Run Ruff lint checks' \
		'  type            Run ty type checks' \
		'  test            Run Python unit tests' \
		'  website-test-unit Run pure JavaScript tests with node:test' \
		'  check-fast      Run formatting, lint, type, and unit tests' \
		'  check           Run the existing full quality gate' \
		'  test-browser    Run Chromium smoke tests against generated output' \
		'  site-smoke      Generate and crawl production website output' \
		'  baseline        Write a reproducible JSON tooling baseline to output/' \
		'  benchmark       Run opt-in performance benchmarks' \
		'  benchmark-synthetic Run the benchmark with deterministic synthetic data' \
		'  benchmark-record Record the dated baseline from DATABASE=<ignored copy>' \
		'  prototype-checkpointed-state Evaluate ADR-0001 in a temporary database' \
		'  prototype-checkpointed-state-targeted Run targeted ADR-0001 evidence checks' \
		'  clean-generated Remove only reproducible website output artifacts'

bootstrap: sync website-install

doctor:
	./scripts/runtime_doctor.sh

sync:
	$(UV) sync --locked --group dev

format:
	$(UV) run ruff format

format-check:
	$(UV) run ruff format --check

lint:
	$(UV) run ruff check

type:
	$(UV) run ty check

test:
	$(UV) run pytest

website-install:
	$(NPM) ci --prefix assets/website

website-lint:
	$(NPM) --prefix assets/website run lint

website-test-unit:
	$(NPM) --prefix assets/website run test:unit

website-build:
	$(NPM) --prefix assets/website run build

check-fast: format-check lint type test

# Keep this dependency list stable: the browser and smoke checks are opt-in.
check: format-check lint type test website-lint website-test-unit website-build

site-generate: website-build
	$(UV) run monitor deploy --force

test-browser: site-generate
	$(NPM) --prefix assets/website exec playwright install chromium
	$(NPM) --prefix assets/website run test:e2e

site-smoke: site-generate
	@mkdir -p output
	$(UV) run python scripts/site_smoke.py --json output/generated-site-crawl.json

baseline:
	@mkdir -p output
	$(UV) run python scripts/write_baseline.py output/tooling-baseline.json

benchmark: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy> or use make benchmark-synthetic' >&2; exit 2; }
	@mkdir -p output
	$(UV) run python scripts/benchmark_performance.py --database "$(DATABASE)" --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-database:
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p output
	$(UV) run python scripts/benchmark_performance.py --database "$(DATABASE)" --mode database --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-website: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p output
	$(UV) run python scripts/benchmark_performance.py --database "$(DATABASE)" --mode website --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-browser: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p output
	$(UV) run python scripts/benchmark_performance.py --database "$(DATABASE)" --mode browser --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-synthetic: website-build
	@mkdir -p output
	$(UV) run python scripts/benchmark_performance.py --synthetic --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-record: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p docs/baselines
	$(UV) run python scripts/benchmark_performance.py --database "$(DATABASE)" --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output docs/baselines/performance-2026-07-29.json --markdown docs/baselines/performance-2026-07-29.md

benchmark-record-deploy: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p docs/baselines
	$(UV) run python scripts/benchmark_performance.py --database "$(DATABASE)" --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --deploy-preview --output docs/baselines/performance-2026-07-29.json --markdown docs/baselines/performance-2026-07-29.md

prototype-checkpointed-state:
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p output
	$(UV) run python scripts/evaluate_checkpointed_state.py --database "$(DATABASE)" \
		--output "$(PROTOTYPE_OUTPUT)" --markdown "$(PROTOTYPE_MARKDOWN)" \
		$(if $(PROTOTYPE_SEMESTER),--semester "$(PROTOTYPE_SEMESTER)")

prototype-checkpointed-state-targeted:
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@test -n "$(RAW_DIR)" || { echo 'Set RAW_DIR=<retained registrar XLS directory>' >&2; exit 2; }
	@mkdir -p output
	$(UV) run python scripts/evaluate_checkpointed_state.py --database "$(DATABASE)" \
		--output "$(PROTOTYPE_OUTPUT)" --markdown "$(PROTOTYPE_MARKDOWN)" \
		--targeted-samples "$(PROTOTYPE_TARGETED_SAMPLES)" --raw-dir "$(RAW_DIR)" \
		--failure-injection \
		$(if $(PROTOTYPE_SEMESTER),--semester "$(PROTOTYPE_SEMESTER)")

clean-generated:
	rm -f assets/website/public/*.html assets/website/public/*.json assets/website/public/_headers assets/website/public/robots.txt assets/website/public/.checksums.json
	rm -rf assets/website/public/assets assets/website/public/courses assets/website/public/data assets/website/public/.vite
