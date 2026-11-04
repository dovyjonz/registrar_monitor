#!/usr/bin/env bash
# Restore a database from its Litestream replica in R2 (read-only against R2).
#
# Usage: scripts/restore_litestream.sh <db-path> <output-path> [timestamp]
#   scripts/restore_litestream.sh data/enrollment_fall_2026.db /tmp/restored.db
#   scripts/restore_litestream.sh data/telegram_bot.db /tmp/bot.db 2026-11-01T12:00:00Z
#
# Restores to <output-path> (never over the live database); stop the writer and
# move the file into place yourself. Requires the .env variables from
# .env.example. The optional timestamp performs a point-in-time restore.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
    sed -n '2,10p' "$0" >&2
    exit 2
fi

db_path="$1"
output="$2"
timestamp="${3:-}"

project_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$project_root/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$project_root/.env"
    set +a
fi

if [[ -e "$output" ]]; then
    echo "Refusing to overwrite existing $output" >&2
    exit 1
fi

args=(-o "$output")
if [[ -n "$timestamp" ]]; then
    args+=(-timestamp "$timestamp")
fi
cd "$project_root"

# Directory-replicated databases are not found by path in the config, so restore
# them from their replica URL. Credentials still come from the environment.
db_name="$(basename -- "$db_path")"
if [[ "$db_name" == enrollment_*.db ]]; then
    : "${R2_BUCKET:?R2_BUCKET is required}"
    : "${R2_ACCOUNT_ID:?R2_ACCOUNT_ID is required}"
    exec litestream restore "${args[@]}" \
        "s3://$R2_BUCKET/enrollment/$db_name?endpoint=https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com&region=auto"
fi
exec litestream restore -config "$project_root/scripts/litestream.yml" "${args[@]}" "$db_path"
