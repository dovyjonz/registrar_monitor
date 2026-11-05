#!/usr/bin/env bash
# Pause the Healthchecks.io heartbeat check while the scheduler is deliberately
# stopped (end of a registration period). The next successful ping from
# `monitor schedule` resumes the check automatically, so there is no resume step.
#
# Usage: scripts/healthcheck_pause.sh
# Reads HEALTHCHECKS_API_KEY (a management API key, not the ping URL) and
# HEALTHCHECKS_CHECK_UUID from the environment or .env. Prints no secrets.
set -euo pipefail

cd "$(dirname "$0")/.."

# Read only the two keys we need; do not source or print .env.
read_env() {
    local name="$1" value="${!1:-}"
    if [ -z "$value" ] && [ -f .env ]; then
        value="$(sed -n "s/^${name}=//p" .env | tail -n 1)"
        value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
    fi
    printf '%s' "$value"
}

api_key="$(read_env HEALTHCHECKS_API_KEY)"
check_uuid="$(read_env HEALTHCHECKS_CHECK_UUID)"
if [ -z "$api_key" ] || [ -z "$check_uuid" ]; then
    echo "error: HEALTHCHECKS_API_KEY and HEALTHCHECKS_CHECK_UUID must be set" >&2
    exit 1
fi
if [[ ! "$check_uuid" =~ ^[0-9a-fA-F-]{36}$ ]]; then
    echo "error: HEALTHCHECKS_CHECK_UUID is not a UUID" >&2
    exit 1
fi

# The key goes through curl's config on stdin so it never appears in argv.
status="$(printf 'header = "X-Api-Key: %s"\n' "$api_key" |
    curl -sS -m 20 -o /dev/null -w '%{http_code}' -K - -X POST \
        "https://healthchecks.io/api/v3/checks/$check_uuid/pause")"
if [ "$status" != "200" ]; then
    echo "error: Healthchecks pause failed (HTTP $status)" >&2
    exit 1
fi
echo "Healthchecks check paused; it resumes on the next successful ping."
