#!/usr/bin/env bash
# Fetch the registrar's certificate chain and write the intermediate and root
# certificates (everything except the leaf) as a PEM bundle for
# data_source.ca_bundle. Run on the host that polls the registrar.
#
# Usage: scripts/fetch_registrar_ca.sh [host] [output]
# Defaults: registrar.nu.edu.kz and data/registrar-chain.pem (the ca_bundle
# path in settings.toml).
set -euo pipefail

cd "$(dirname "$0")/.."
host="${1:-registrar.nu.edu.kz}"
output="${2:-data/registrar-chain.pem}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

openssl s_client -showcerts -servername "$host" -connect "$host:443" \
    </dev/null 2>/dev/null >"$tmp/session.txt" || true

# Split the PEM blocks; certificate 1 is the leaf and is not pinned.
awk -v dir="$tmp" '
    /-----BEGIN CERTIFICATE-----/ { n++; file = sprintf("%s/cert%d.pem", dir, n) }
    file { print > file }
    /-----END CERTIFICATE-----/ { file = "" }
' "$tmp/session.txt"

if [ ! -f "$tmp/cert2.pem" ]; then
    echo "error: $host did not send an intermediate certificate" >&2
    exit 1
fi

mkdir -p "$(dirname "$output")"
cat "$tmp"/cert[2-9].pem >"$output.new"
mv "$output.new" "$output"
chmod 644 "$output"

echo "Wrote $(grep -c 'BEGIN CERTIFICATE' "$output") certificate(s) to $output"
echo "Verifying the chain against the bundle..."
openssl s_client -CAfile "$output" -servername "$host" -connect "$host:443" \
    </dev/null 2>&1 | grep -E "Verify return code" || true
