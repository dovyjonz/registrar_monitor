#!/usr/bin/env bash
# SessionStart hook: prepare dependencies in Claude Code cloud sessions only.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"

command -v uv >/dev/null || pip install --quiet uv
uv python install "$(cat .python-version)" >/dev/null
make bootstrap >/dev/null
echo "Cloud session ready: Python and npm dependencies installed. Use git; jj is not installed here."
