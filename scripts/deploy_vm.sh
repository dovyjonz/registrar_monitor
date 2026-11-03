#!/usr/bin/env bash
# Deploy a named revision to the VM and restart one systemd unit.
#
# Usage: scripts/deploy_vm.sh <revision> <unit>
#   scripts/deploy_vm.sh main registrarmonitor.service
#
# Environment:
#   VM_HOST    (required) SSH host
#   VM_PATH    (required) checkout path on the VM
#   VM_USER    (default: current user)
#   VM_REMOTE  (default: origin) git remote the VM fetches from
#
# The revision must already be pushed to that remote (branch, tag, or sha; for
# jj, push the bookmark first). Run only when explicitly authorized.
set -euo pipefail

if [[ $# -ne 2 ]]; then
    sed -n '2,13p' "$0" >&2
    exit 2
fi

revision="$1"
unit="$2"
: "${VM_HOST:?VM_HOST is required}"
: "${VM_PATH:?VM_PATH is required}"
vm_user="${VM_USER:-$(id -un)}"
remote="${VM_REMOTE:-origin}"

if [[ ! "$revision" =~ ^[A-Za-z0-9._/-]+$ || ! "$unit" =~ ^[A-Za-z0-9@._-]+\.(service|timer)$ ]]; then
    echo "Invalid revision or unit name" >&2
    exit 2
fi

sha="$(git rev-parse --verify "$revision^{commit}")"
echo "Deploy plan:"
echo "  revision : $revision ($sha)"
echo "  target   : $vm_user@$VM_HOST:$VM_PATH"
echo "  steps    : git fetch $remote; git checkout --detach $sha; uv sync --locked; sudo systemctl restart $unit"
echo "  untouched: all other units, databases, .env"

ssh "$vm_user@$VM_HOST" bash -s -- "$VM_PATH" "$remote" "$sha" "$unit" <<'REMOTE'
set -euo pipefail
path="$1"; remote="$2"; sha="$3"; unit="$4"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
cd "$path"
git fetch --quiet "$remote"
git checkout --quiet --detach "$sha"
uv sync --locked
sudo systemctl restart "$unit"
echo "Deployed revision: $(git rev-parse HEAD)"
systemctl is-active "$unit"
REMOTE
