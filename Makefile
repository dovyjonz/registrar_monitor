.DEFAULT_GOAL := help

.PHONY: help bootstrap doctor sync format format-check lint type test website-install website-lint website-build check-fast check site-generate test-browser site-smoke baseline benchmark clean-generated

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
		'  check-fast      Run formatting, lint, type, and unit tests' \
		'  check           Run the existing full quality gate' \
		'  test-browser    Run Chromium smoke tests against generated output' \
		'  site-smoke      Generate and crawl production website output' \
		'  baseline        Write a reproducible JSON tooling baseline to output/' \
		'  benchmark       Run opt-in performance benchmarks' \
		'  clean-generated Remove only reproducible website output artifacts'

bootstrap: sync website-install

doctor:
	@set -eu; \
	command -v uv >/dev/null; command -v jj >/dev/null; command -v codex >/dev/null; command -v node >/dev/null; command -v npm >/dev/null; \
	python_version="$$(cat .python-version)"; node_version="$$(cat .node-version)"; codex_version="$$(codex --version 2>&1 | sed -n 's/.* \([0-9][0-9.]*\).*/\1/p' | head -n 1)"; \
	uv python find "$$python_version" >/dev/null || { echo "Python $$python_version is not installed" >&2; exit 1; }; \
	[ "$$(node --version)" = "v$$node_version" ] || { echo "Node must be v$$node_version; found $$(node --version)" >&2; exit 1; }; \
	case "$$(npm --version)" in 11.*) ;; *) echo "npm 11.x is required; found $$(npm --version)" >&2; exit 1;; esac; \
	awk -v have="$$codex_version" -v need="0.144.0" 'BEGIN { split(have,h,"."); split(need,n,"."); for (i=1;i<=3;i++) { if ((h[i]+0) > (n[i]+0)) exit 0; if ((h[i]+0) < (n[i]+0)) exit 1 } }' || { echo "Codex CLI >= 0.144.0 is required; found $$codex_version" >&2; exit 1; }; \
	test -f pyproject.toml; test -f assets/website/package.json; test -f settings.toml; uv lock --check; \
	printf '%s\n' "Python pin: $$python_version" "Node pin: $$node_version" "npm: $$(npm --version)" "uv: $$(uv --version)" "Jujutsu: $$(jj --version)" "Codex: $$(codex --version 2>&1 | tail -n 1)"; \
	if test -f .env; then echo '.env: present'; else echo '.env: absent (Telegram and deploy secrets are optional)'; fi

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

website-build:
	npm --prefix assets/website run build

check-fast: format-check lint type test

# Keep this dependency list stable: the browser and smoke checks are opt-in.
check: format-check lint type test website-lint website-build

site-generate: website-build
	uv run monitor deploy --force

test-browser: site-generate
	npm --prefix assets/website exec playwright install chromium
	npm --prefix assets/website run test:browser

site-smoke: site-generate
	uv run python scripts/site_smoke.py

baseline:
	@mkdir -p output
	uv run python scripts/write_baseline.py output/tooling-baseline.json

benchmark:
	uv run python scripts/benchmark_downloader.py

clean-generated:
	rm -f assets/website/public/*.html assets/website/public/*.json assets/website/public/_headers assets/website/public/robots.txt assets/website/public/.checksums.json
	rm -rf assets/website/public/assets assets/website/public/courses assets/website/public/.vite
