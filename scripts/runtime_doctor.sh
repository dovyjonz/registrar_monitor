#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

# `--vm` prints the read-only OS tuning report (swap and zram, sysctls, cgroup
# delegation for the build scope, journald usage, unit limits) and nothing else.
if [[ "${1:-}" == "--vm" ]]; then
    exec "$SCRIPT_DIR/tune_vm.sh" --status
fi

# Same binary the systemd units run.
MONITOR="$PROJECT_ROOT/.venv/bin/monitor"
if [[ ! -x "$MONITOR" ]]; then
    printf '%s\n' \
        "$MONITOR was not found; run scripts/setup_vps.sh or deploy first (uv sync --locked)." \
        >&2
    exit 127
fi

cd "$PROJECT_ROOT"
exec "$MONITOR" doctor "$@"
