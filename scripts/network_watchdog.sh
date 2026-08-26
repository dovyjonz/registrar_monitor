#!/bin/bash
set -eu

METADATA_URL="http://169.254.169.254/computeMetadata/v1/instance/id"

metadata_is_reachable() {
    curl --fail --silent --show-error \
        --connect-timeout 3 \
        --max-time 5 \
        --header "Metadata-Flavor: Google" \
        "$METADATA_URL" >/dev/null
}

if metadata_is_reachable; then
    exit 0
fi

sleep 2
if metadata_is_reachable; then
    exit 0
fi

echo "Google metadata is unreachable; restarting systemd-networkd" >&2
timeout --signal=TERM --kill-after=2s 10s \
    systemctl restart systemd-networkd.service
sleep 3

if ! metadata_is_reachable; then
    echo "Google metadata is still unreachable after network restart" >&2
    exit 1
fi

echo "Google metadata connectivity recovered" >&2
