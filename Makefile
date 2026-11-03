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

.PHONY: help bootstrap check-fast check test-browser site-generate worker-check clean

help:
	@printf '%s\n' \
		'Available targets:' \
		'  bootstrap       Install the pinned Python and Node dependencies' \
		'  check-fast      Run Ruff format check, Ruff lint, ty, and pytest' \
		'  check           Run check-fast plus frontend lint, unit tests, and build' \
		'  test-browser    Run Chromium smoke tests against generated output' \
		'  site-generate   Build the dashboard and generate the production site' \
		'  worker-check    Run preview Worker types, tests, TypeScript, and dry-run' \
		'  clean           Remove reproducible website output artifacts'

bootstrap:
	$(UV) sync --locked --group dev
	$(NPM) ci --prefix frontend
	$(NPM) ci --prefix workers/preview-images

check-fast:
	$(UV) run ruff format --check
	$(UV) run ruff check
	$(UV) run ty check
	$(UV) run pytest

check: check-fast
	$(NPM) --prefix frontend run lint
	$(NPM) --prefix frontend run test:unit
	$(NPM) --prefix frontend run build

site-generate:
	$(NPM) --prefix frontend run build
	$(UV) run monitor deploy --force

test-browser:
	$(NPM) --prefix frontend run build
	$(UV) run python scripts/generate_smoke_site.py
	$(NPM) --prefix frontend exec -- playwright install chromium
	$(NPM) --prefix frontend run test:e2e

worker-check:
	$(NPM) --prefix workers/preview-images run check

clean:
	rm -f frontend/public/*.html frontend/public/*.json frontend/public/_headers frontend/public/robots.txt frontend/public/.checksums.json
	rm -rf frontend/public/assets frontend/public/courses frontend/public/data frontend/public/.vite
