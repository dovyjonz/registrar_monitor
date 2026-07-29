.DEFAULT_GOAL := help

.PHONY: help bootstrap doctor sync format format-check lint type test website-install website-lint website-test-unit website-build check-fast check site-generate test-browser site-smoke baseline benchmark benchmark-database benchmark-website benchmark-browser benchmark-synthetic benchmark-record benchmark-record-deploy clean-generated

PERF_COLD ?= 10
PERF_WARM ?= 20
PERF_OUTPUT ?= output/performance-baseline.json

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
		'  clean-generated Remove only reproducible website output artifacts'

bootstrap: sync website-install

doctor:
	uv run monitor doctor

sync:
	uv sync --locked --group dev

format:
	uv run ruff format

format-check:
	uv run ruff format --check

lint:
	uv run ruff check

type:
	uv run ty check

test:
	uv run pytest

website-install:
	npm ci --prefix assets/website

website-lint:
	npm --prefix assets/website run lint

website-test-unit:
	npm --prefix assets/website run test:unit

website-build:
	npm --prefix assets/website run build

check-fast: format-check lint type test

# Keep this dependency list stable: the browser and smoke checks are opt-in.
check: format-check lint type test website-lint website-test-unit website-build

site-generate: website-build
	uv run monitor deploy --force

test-browser: site-generate
	npm --prefix assets/website exec playwright install chromium
	npm --prefix assets/website run test:e2e

site-smoke: site-generate
	@mkdir -p output
	uv run python scripts/site_smoke.py --json output/generated-site-crawl.json

baseline:
	@mkdir -p output
	uv run python scripts/write_baseline.py output/tooling-baseline.json

benchmark: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy> or use make benchmark-synthetic' >&2; exit 2; }
	@mkdir -p output
	uv run python scripts/benchmark_performance.py --database "$(DATABASE)" --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-database:
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p output
	uv run python scripts/benchmark_performance.py --database "$(DATABASE)" --mode database --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-website: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p output
	uv run python scripts/benchmark_performance.py --database "$(DATABASE)" --mode website --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-browser: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p output
	uv run python scripts/benchmark_performance.py --database "$(DATABASE)" --mode browser --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-synthetic: website-build
	@mkdir -p output
	uv run python scripts/benchmark_performance.py --synthetic --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output "$(PERF_OUTPUT)"

benchmark-record: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p docs/baselines
	uv run python scripts/benchmark_performance.py --database "$(DATABASE)" --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --output docs/baselines/performance-2026-07-29.json --markdown docs/baselines/performance-2026-07-29.md

benchmark-record-deploy: website-build
	@test -n "$(DATABASE)" || { echo 'Set DATABASE=<ignored SQLite copy>' >&2; exit 2; }
	@mkdir -p docs/baselines
	uv run python scripts/benchmark_performance.py --database "$(DATABASE)" --cold-iterations "$(PERF_COLD)" --warm-iterations "$(PERF_WARM)" --deploy-preview --output docs/baselines/performance-2026-07-29.json --markdown docs/baselines/performance-2026-07-29.md

clean-generated:
	rm -f assets/website/public/*.html assets/website/public/*.json assets/website/public/_headers assets/website/public/robots.txt assets/website/public/.checksums.json
	rm -rf assets/website/public/assets assets/website/public/courses assets/website/public/.vite
