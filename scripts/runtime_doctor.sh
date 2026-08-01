#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
USER_HOME="${HOME:-}"

if [[ -n "${UV_BIN:-}" ]]; then
    UV_COMMAND="$UV_BIN"
elif UV_COMMAND="$(command -v uv 2>/dev/null)"; then
    :
elif [[ -x "$USER_HOME/.local/bin/uv" ]]; then
    UV_COMMAND="$USER_HOME/.local/bin/uv"
elif [[ -x "$USER_HOME/.cargo/bin/uv" ]]; then
    UV_COMMAND="$USER_HOME/.cargo/bin/uv"
else
    printf '%s\n' \
        'uv was not found; run scripts/setup_vps.sh first or set UV_BIN to its path.' \
        >&2
    exit 127
fi

cd "$PROJECT_ROOT"
exec "$UV_COMMAND" run --locked --no-sync --no-cache monitor doctor "$@"
