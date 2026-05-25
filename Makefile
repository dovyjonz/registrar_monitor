.PHONY: help sync format format-check lint type test website-install website-lint website-build check

help:
	@printf '%s\n' \
		'Available targets:' \
		'  sync            Install Python dev dependencies with uv' \
		'  format          Format Python code with ruff' \
		'  format-check    Check Python formatting' \
		'  lint            Run ruff lint checks' \
		'  type            Run ty type checks' \
		'  test            Run pytest' \
		'  website-install Install website dependencies with npm ci' \
		'  website-lint    Run website lint checks' \
		'  website-build   Build website assets' \
		'  check           Run format-check, lint, type, tests, website lint, and website build'

sync:
	uv sync --group dev

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

check: format-check lint type test website-lint website-build
