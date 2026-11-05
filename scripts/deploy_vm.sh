#!/usr/bin/env bash
# Deploy to the VM in two separate steps. Run only when explicitly authorized.
#
# Usage:
#   scripts/deploy_vm.sh sync <revision>   fetch + checkout, uv sync, npm ci if the
#                                          lockfile changed; restarts nothing
#   scripts/deploy_vm.sh restart <unit>    restart the unit only if it is active,
#                                          then wait until it is healthy
#
# Environment:
#   VM_HOST    (required) SSH host, or the instance name when VM_SSH=gcloud
#   VM_PATH    (required) checkout path on the VM
#   VM_USER    (default: current user) SSH operator account
#   VM_REMOTE  (default: origin) git remote the VM fetches from
#   VM_SSH     (default: ssh) set to "gcloud" to use `gcloud compute ssh`, which
#              manages its own key and known-hosts; extra flags (project, zone)
#              go in GCLOUD_SSH_ARGS, e.g. "--project registrarmonitor --zone us-east1-c"
#   HEALTH_WAIT (default: 30) seconds a restarted unit must stay active
#
# The revision must already be pushed to the remote. A stopped unit is never
# started (in particular the scheduler, which is stopped between registration
# periods); start it by hand when authorized.
set -euo pipefail

usage() {
    sed -n '2,23p' "$0" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
mode="$1"
arg="$2"
: "${VM_HOST:?VM_HOST is required}"
: "${VM_PATH:?VM_PATH is required}"
vm_user="${VM_USER:-$(id -un)}"
remote="${VM_REMOTE:-origin}"
health_wait="${HEALTH_WAIT:-30}"

[[ "$health_wait" =~ ^[0-9]+$ ]] || { echo "HEALTH_WAIT must be a number" >&2; exit 2; }

run_remote() {
    # Reads the script from stdin; arguments are quoted for the remote shell.
    local quoted
    quoted="$(printf '%q ' "$@")"
    case "${VM_SSH:-ssh}" in
    ssh) ssh "$vm_user@$VM_HOST" "bash -s -- $quoted" ;;
    gcloud)
        # shellcheck disable=SC2086  # GCLOUD_SSH_ARGS is a flag list
        gcloud compute ssh "$vm_user@$VM_HOST" ${GCLOUD_SSH_ARGS:-} --quiet \
            --command "bash -s -- $quoted"
        ;;
    *) echo "VM_SSH must be ssh or gcloud" >&2; exit 2 ;;
    esac
}

case "$mode" in
sync)
    revision="$arg"
    [[ "$revision" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "Invalid revision" >&2; exit 2; }
    sha="$(git rev-parse --verify "$revision^{commit}")"
    echo "Sync plan: $revision ($sha) -> $vm_user@$VM_HOST:$VM_PATH"
    echo "  git fetch/checkout; uv sync --locked; npm ci --prefix frontend if package-lock.json changed"
    echo "  no unit is restarted or started"
    run_remote "$VM_PATH" "$remote" "$sha" <<'REMOTE'
set -euo pipefail
umask 022
path="$1"; remote="$2"; sha="$3"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
cd "$path"
old="$(git rev-parse HEAD)"
git fetch --quiet "$remote"
git checkout --quiet --detach "$sha"
uv sync --locked
# Installs run here, as the SSH operator, never as the runtime user. Re-run only
# when the lockfile changed (or node_modules is missing) so ownership stays stable.
if [ ! -d frontend/node_modules ] ||
    ! git diff --quiet "$old" "$sha" -- frontend/package-lock.json frontend/package.json; then
    npm ci --prefix frontend
    chmod -R g+rX frontend/node_modules
fi
echo "Synced revision: $(git rev-parse HEAD)"
echo "Running processes still use the old code until their unit is restarted."
REMOTE
    ;;
restart)
    unit="$arg"
    [[ "$unit" =~ ^[A-Za-z0-9@._-]+\.(service|timer)$ ]] || { echo "Invalid unit name" >&2; exit 2; }
    echo "Restart plan: $unit on $vm_user@$VM_HOST (only if active; waits ${health_wait}s)"
    run_remote "$unit" "$health_wait" <<'REMOTE'
set -euo pipefail
unit="$1"; wait_s="$2"
state="$(systemctl is-active "$unit" || true)"
if [ "$state" != "active" ]; then
    echo "$unit is $state; leaving it alone (never started by deploy)."
    exit 0
fi
restarts_before="$(systemctl show -p NRestarts --value "$unit" 2>/dev/null || echo 0)"
sudo systemctl restart "$unit"
for ((i = 0; i < wait_s; i++)); do
    sleep 1
    state="$(systemctl is-active "$unit" || true)"
    restarts="$(systemctl show -p NRestarts --value "$unit" 2>/dev/null || echo 0)"
    if [ "$state" != "active" ] || [ "$restarts" != "$restarts_before" ]; then
        echo "$unit unhealthy after restart (state=$state, auto-restarts=$restarts)" >&2
        exit 1
    fi
done
echo "$unit active and stable for ${wait_s}s (the bot exposes no health endpoint)."
REMOTE
    ;;
*) usage ;;
esac
