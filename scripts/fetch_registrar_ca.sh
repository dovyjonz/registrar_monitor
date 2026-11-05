#!/usr/bin/env bash
# Fetch the registrar's certificate chain and write the intermediate and root
# certificates (everything except the leaf) as a PEM bundle for
# data_source.ca_bundle. Run on the host that polls the registrar.
#
# Usage: scripts/fetch_registrar_ca.sh [host] [output]
# Defaults: registrar.nu.edu.kz and data/registrar-chain.pem (the ca_bundle
# path in settings.toml).
set -euo pipefail  # pipefail matters: the awk/sed/grep pipelines below must not hide errors

cd "$(dirname "$0")/.."
host="${1:-registrar.nu.edu.kz}"
output="${2:-data/registrar-chain.pem}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Fail loudly: keep s_client's stderr and require at least one certificate.
if ! openssl s_client -showcerts -servername "$host" -connect "$host:443" \
    </dev/null >"$tmp/session.txt" 2>"$tmp/s_client.err"; then
    echo "error: openssl s_client could not connect to $host:443" >&2
    cat "$tmp/s_client.err" >&2
    exit 1
fi
if ! grep -q -- '-----BEGIN CERTIFICATE-----' "$tmp/session.txt"; then
    echo "error: $host:443 sent no certificates" >&2
    cat "$tmp/s_client.err" >&2
    exit 1
fi

# Split the PEM blocks; certificate 1 is the leaf and is not pinned.
awk -v dir="$tmp" '
    /-----BEGIN CERTIFICATE-----/ { n++; file = sprintf("%s/cert%d.pem", dir, n) }
    file { print > file }
    /-----END CERTIFICATE-----/ { file = "" }
' "$tmp/session.txt"

# Some servers send only the leaf. Follow its issuer link, and because that link
# is plain HTTP, accept the result only if it chains to a trusted root.
if [ ! -f "$tmp/cert2.pem" ]; then
    issuer_url="$(openssl x509 -in "$tmp/cert1.pem" -noout -ext authorityInfoAccess |
        sed -n 's/.*CA Issuers - URI://p' | head -n 1)"
    if [ -z "$issuer_url" ]; then
        echo "error: $host sent no intermediate and its leaf names no issuer URL" >&2
        exit 1
    fi
    curl -fsS -m 30 -o "$tmp/issuer.crt" "$issuer_url"
    openssl x509 -inform DER -in "$tmp/issuer.crt" -out "$tmp/cert2.pem" 2>/dev/null ||
        openssl x509 -in "$tmp/issuer.crt" -out "$tmp/cert2.pem"
    if ! openssl verify -CAfile "$(.venv/bin/python -m certifi)" \
        -untrusted "$tmp/cert2.pem" "$tmp/cert1.pem" >/dev/null; then
        echo "error: certificate from $issuer_url does not chain to a trusted root" >&2
        exit 1
    fi
fi

# Every pinned block must parse as a certificate before it replaces the bundle.
cat "$tmp"/cert[2-9].pem >"$tmp/bundle.pem"
if [ ! -s "$tmp/bundle.pem" ]; then
    echo "error: no intermediate or root certificate to write" >&2
    exit 1
fi
for cert in "$tmp"/cert[2-9].pem; do
    if ! openssl x509 -in "$cert" -noout 2>/dev/null; then
        echo "error: $(basename "$cert") is not a valid PEM certificate" >&2
        exit 1
    fi
done

mkdir -p "$(dirname "$output")"
cp "$tmp/bundle.pem" "$output.new"
mv "$output.new" "$output"
chmod 644 "$output"

echo "Wrote $(grep -c 'BEGIN CERTIFICATE' "$output") certificate(s) to $output"
echo "Verifying the chain against the bundle..."
verify="$(openssl s_client -partial_chain -CAfile "$output" -servername "$host" \
    -connect "$host:443" </dev/null 2>&1 | grep -E "Verify return code" || true)"
echo "$verify"
if [ -z "$verify" ] || ! grep -q 'return code: 0 ' <<<"$verify"; then
    echo "error: chain did not verify against $output" >&2
    exit 1
fi
