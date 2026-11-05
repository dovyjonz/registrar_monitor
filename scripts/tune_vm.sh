#!/usr/bin/env bash
# Tune the production VM's operating system for the Registrar Monitor workload
# (1 GB RAM, small slow disk, shared-core CPU): compressed swap, writeback and
# cache sysctls, bounded journald, off-peak apt maintenance, and cgroup
# controller delegation for the website build scope.
#
#   scripts/tune_vm.sh                  dry run: current state plus the plan
#   scripts/tune_vm.sh --status         read-only state report, no sudo
#   scripts/tune_vm.sh --apply          make the changes (needs sudo)
#
# It never restarts, stops, or reconfigures a Registrar Monitor unit, never edits
# /etc/fstab or mount options, never touches networking or google-guest-agent,
# and never reads .env or prints environment values. Re-running is safe: files
# whose content already matches are left alone. Revert list: docs/operations.md,
# "VM tuning".
set -euo pipefail

APPLY=0
STATUS_ONLY=0
DISABLE_OSCONFIG=0
BFQ=0
SKIP=","

# Tuning targets. Edit here, not in the steps.
ZRAM_PERCENT=50                 # of RAM; disk swap stays as the fallback
ZRAM_ALGO=zstd
ZRAM_PRIORITY=100               # disk swap is -2
SWAPPINESS=100                  # with zram, swapping is cheap; only with zram
PAGE_CLUSTER=0                  # single-page swap-in; only with zram
VFS_CACHE_PRESSURE=50           # keep dentries/inodes: the disk is slow
DIRTY_BACKGROUND_BYTES=16777216 # 16 MiB: start writeback early ...
DIRTY_BYTES=67108864            # ... and cap stalls at 64 MiB of dirty pages
JOURNAL_MAX_USE=200M
JOURNAL_RETENTION=14day
TZ_NAME=Asia/Almaty
DELEGATE_CONTROLLERS="memory pids cpu io"
PROJECT_UNITS=(registrarmonitor registrarmonitor-bot registrarmonitor-tunnel registrarmonitor-litestream)

RUNTIME_USER="${RUNTIME_USER:-$(id -un)}"

usage() {
    cat <<'EOF'
Usage: scripts/tune_vm.sh [--apply] [--status] [options]

  (default)            dry run: show current values and what --apply would change
  --apply              apply the plan with sudo (idempotent)
  --status             only print the read-only state report (no sudo)
  --skip LIST          comma list of steps to skip:
                       zram,sysctl,thp,journald,timers,delegation,osconfig,bfq
  --disable-osconfig   also disable google-osconfig-agent (saves RAM; GCP OS
                       Config patching and inventory stop working)
  --bfq                also switch the disk I/O scheduler to bfq so the nice/ionice
                       idle class and IOWeight used by the build actually apply
  -h, --help           this text

Environment: RUNTIME_USER (default: invoking user) names the account whose user
manager runs the website build scope.
EOF
}

while (($#)); do
    case "$1" in
        --apply) APPLY=1 ;;
        --status) STATUS_ONLY=1 ;;
        --disable-osconfig) DISABLE_OSCONFIG=1 ;;
        --bfq) BFQ=1 ;;
        --skip)
            shift
            [[ $# -gt 0 ]] || { echo "--skip needs a value" >&2; exit 2; }
            SKIP=",${1},"
            ;;
        -h | --help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [[ ${EUID} -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

say() { printf '%s\n' "$*"; }
flag() { printf '  [%s] %s\n' "$1" "$2"; }
step_enabled() { [[ "$SKIP" != *",$1,"* ]]; }
as_root() { ${SUDO[@]+"${SUDO[@]}"} "$@"; }

# act "description" command...: run as root with --apply, otherwise only print.
act() {
    local desc=$1
    shift
    if ((APPLY)); then
        say "  apply: $desc"
        as_root "$@"
    else
        say "  would: $desc"
    fi
}

# act_sh "description" 'shell snippet': same, for redirections and globs.
act_sh() {
    local desc=$1 snippet=$2
    if ((APPLY)); then
        say "  apply: $desc"
        as_root sh -c "$snippet"
    else
        say "  would: $desc"
    fi
}

NEED_RELOAD=0
FILE_CHANGED=0
NOTES=()

# write_file PATH MODE CONTENT: create or update a root-owned file only when the
# content differs. Sets FILE_CHANGED=1 when it changed (or would change).
write_file() {
    local path=$1 mode=$2 content=$3 current=""
    FILE_CHANGED=0
    if [[ -r $path ]]; then
        current=$(<"$path")
    fi
    if [[ -e $path && "$current" == "$content" ]]; then
        say "  ok:    $path unchanged"
        return 0
    fi
    FILE_CHANGED=1
    if ((APPLY)); then
        say "  apply: write $path"
        as_root install -d -m 0755 "$(dirname "$path")"
        printf '%s\n' "$content" | as_root tee "$path" >/dev/null
        as_root chmod "$mode" "$path"
    else
        say "  would: write $path"
        printf '%s\n' "$content" | sed 's/^/           | /'
    fi
}

unit_exists() {
    [[ "$(systemctl show -p LoadState --value "$1" 2>/dev/null || true)" == "loaded" ]]
}

swap_names() { swapon --noheadings --show=NAME 2>/dev/null || true; }

# --------------------------------------------------------------------------
# Read-only state report
# --------------------------------------------------------------------------

controllers_of() {
    local uid=$1
    local f="/sys/fs/cgroup/user.slice/user-${uid}.slice/user@${uid}.service/cgroup.controllers"
    [[ -r $f ]] && cat "$f"
}

status_report() {
    local names key val target uid list c missing

    say "== Memory and swap =="
    awk '/^MemTotal:/ {printf "  RAM: %d MiB\n", $2 / 1024}' /proc/meminfo
    awk '/^SwapTotal:/ {printf "  swap total: %d MiB\n", $2 / 1024}' /proc/meminfo
    swapon --show=NAME,TYPE,SIZE,USED,PRIO 2>/dev/null | sed 's/^/  /' || true
    names=$(swap_names)
    if [[ $names == */dev/zram* ]]; then
        flag ok "zram swap active"
        if [[ -r /sys/block/zram0/comp_algorithm ]]; then
            say "  zram0 algorithm: $(cat /sys/block/zram0/comp_algorithm)"
        fi
        if [[ -r /sys/block/zram0/mm_stat ]]; then
            awk '{printf "  zram0 stored %.1f MiB uncompressed in %.1f MiB RAM\n", $1/1048576, $3/1048576}' /sys/block/zram0/mm_stat
        fi
    else
        flag warn "no zram swap (disk swap only)"
    fi

    say "== Kernel tunables =="
    for key in vm.swappiness vm.page-cluster vm.vfs_cache_pressure vm.dirty_background_bytes vm.dirty_bytes; do
        val=$(sysctl -n "$key" 2>/dev/null || echo n/a)
        case $key in
            vm.swappiness) target=$SWAPPINESS ;;
            vm.page-cluster) target=$PAGE_CLUSTER ;;
            vm.vfs_cache_pressure) target=$VFS_CACHE_PRESSURE ;;
            vm.dirty_background_bytes) target=$DIRTY_BACKGROUND_BYTES ;;
            vm.dirty_bytes) target=$DIRTY_BYTES ;;
        esac
        if [[ $val == "$target" ]]; then
            flag ok "$key = $val"
        else
            flag info "$key = $val (tune_vm target $target)"
        fi
    done
    if [[ -r /sys/kernel/mm/transparent_hugepage/enabled ]]; then
        say "  transparent hugepages: $(cat /sys/kernel/mm/transparent_hugepage/enabled)"
    fi
    for c in /sys/block/*/queue/scheduler; do
        [[ -r $c ]] || continue
        case $c in */zram* | */loop* | */ram*) continue ;; esac
        say "  I/O scheduler $(basename "$(dirname "$(dirname "$c")")"): $(cat "$c")"
    done

    say "== cgroup delegation for the website build scope =="
    if [[ -r /sys/fs/cgroup/cgroup.controllers ]]; then
        list=$(cat /sys/fs/cgroup/cgroup.controllers)
        if [[ " $list " == *" memory "* ]]; then
            flag ok "root cgroup offers memory controller"
        else
            flag warn "root cgroup has no memory controller ($list)"
        fi
    else
        flag warn "cgroup v2 is not mounted at /sys/fs/cgroup"
    fi
    if uid=$(id -u "$RUNTIME_USER" 2>/dev/null); then
        if list=$(controllers_of "$uid"); then
            missing=""
            for c in $DELEGATE_CONTROLLERS; do
                [[ " $list " == *" $c "* ]] || missing="$missing $c"
            done
            if [[ " $list " != *" memory "* ]]; then
                flag warn "user@${uid}.service does not delegate memory ($list): the build MemoryMax is not enforced"
            elif [[ -n $missing ]]; then
                flag info "user@${uid}.service delegates: $list (missing:$missing; run tune_vm.sh --apply, then restart user@${uid} while idle or reboot)"
            else
                flag ok "user@${uid}.service delegates: $list"
            fi
        else
            flag warn "user manager for $RUNTIME_USER is not running (enable lingering)"
        fi
        if command -v loginctl >/dev/null 2>&1; then
            say "  $(loginctl show-user "$RUNTIME_USER" --property=Linger 2>/dev/null || echo 'Linger=unknown')"
        fi
    fi

    say "== journald =="
    say "  $(journalctl --disk-usage 2>/dev/null || echo 'disk usage unavailable (needs systemd-journal group)')"
    if [[ -e /etc/systemd/journald.conf.d/90-registrarmonitor.conf ]]; then
        flag ok "journald drop-in installed"
    else
        flag info "journald drop-in not installed"
    fi

    say "== Maintenance timers =="
    for c in apt-daily.timer apt-daily-upgrade.timer fstrim.timer man-db.timer; do
        if unit_exists "$c"; then
            say "  $c: $(systemctl is-enabled "$c" 2>/dev/null || true), $(systemctl show -p TimersCalendar --value "$c" 2>/dev/null | tr -s ' ' | head -c 160)"
        fi
    done
    if unit_exists google-osconfig-agent.service; then
        say "  google-osconfig-agent: $(systemctl is-enabled google-osconfig-agent.service 2>/dev/null || true) / $(systemctl is-active google-osconfig-agent.service 2>/dev/null || true)"
    fi

    say "== Project unit limits =="
    local u cur
    for u in "${PROJECT_UNITS[@]}"; do
        if ! unit_exists "$u.service"; then
            say "  $u: not installed"
            continue
        fi
        declare -A p=()
        while IFS='=' read -r key val; do p[$key]=$val; done < <(
            systemctl show "$u.service" -p ActiveState -p MemoryCurrent -p MemoryHigh \
                -p MemoryMax -p CPUWeight -p IOWeight -p OOMScoreAdjust 2>/dev/null || true
        )
        cur=${p[MemoryCurrent]:-}
        if [[ $cur =~ ^[0-9]+$ ]]; then
            cur="$((cur / 1048576)) MiB"
        else
            cur="n/a"
        fi
        say "  $u: ${p[ActiveState]:-?}, mem $cur, high=${p[MemoryHigh]:-?} max=${p[MemoryMax]:-?}, cpuw=${p[CPUWeight]:-?} ioweight=${p[IOWeight]:-?} oom_adj=${p[OOMScoreAdjust]:-?}"
        unset p
    done
}

if ((STATUS_ONLY)); then
    status_report
    exit 0
fi

# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

ZRAM_OK=0

step_zram() {
    say "[zram] compressed swap in RAM (${ZRAM_ALGO}, ${ZRAM_PERCENT}% of RAM, priority ${ZRAM_PRIORITY}; disk swap stays as fallback)"
    local cfg=/etc/default/zramswap names used
    names=$(swap_names)
    if [[ $names == */dev/zram* ]]; then
        ZRAM_OK=1
    elif ! [[ -d /sys/block/zram0 ]] && ! modinfo zram >/dev/null 2>&1; then
        say "  skip:  this kernel has no zram module"
        return 0
    fi

    if ! dpkg -s zram-tools >/dev/null 2>&1; then
        act "apt-get install zram-tools" apt-get install -y --no-install-recommends zram-tools
    else
        say "  ok:    zram-tools installed"
    fi
    ZRAM_OK=1

    if [[ -e $cfg && ! -e $cfg.registrarmonitor.bak ]]; then
        act "back up $cfg to $cfg.registrarmonitor.bak" cp -p "$cfg" "$cfg.registrarmonitor.bak"
    fi
    write_file "$cfg" 0644 "# Managed by scripts/tune_vm.sh (registrar_monitor).
# The package default is saved as ${cfg}.registrarmonitor.bak.
ALGO=${ZRAM_ALGO}
PERCENT=${ZRAM_PERCENT}
PRIORITY=${ZRAM_PRIORITY}"

    names=$(swap_names)
    if [[ $names != */dev/zram* ]]; then
        act "systemctl enable --now zramswap.service" systemctl enable --now zramswap.service
    elif ((FILE_CHANGED)); then
        # Restarting swaps zram off first, which has to pull its pages back in.
        used=$(awk '$1 ~ /^\/dev\/zram/ {u += $4} END {print u + 0}' /proc/swaps)
        if ((used > 65536)); then
            say "  defer: zram holds $((used / 1024)) MiB; the new settings apply at the next reboot"
            NOTES+=("zram settings changed while in use; reboot (or restart zramswap when idle) to apply them")
        else
            act "systemctl restart zramswap.service" systemctl restart zramswap.service
        fi
    fi
    swapon --show=NAME,TYPE,SIZE,USED,PRIO 2>/dev/null | sed 's/^/  now:   /' || true
}

step_sysctl() {
    say "[sysctl] writeback, cache pressure, and (with zram) swap behaviour"
    local conf=/etc/sysctl.d/90-registrarmonitor.conf body
    body="# Managed by scripts/tune_vm.sh (registrar_monitor).
# Slow persistent disk: start writeback early and bound the stall size.
vm.dirty_background_bytes = ${DIRTY_BACKGROUND_BYTES}
vm.dirty_bytes = ${DIRTY_BYTES}
# Keep dentry/inode caches; re-reading them from the slow disk is expensive.
vm.vfs_cache_pressure = ${VFS_CACHE_PRESSURE}"
    if ((ZRAM_OK)); then
        body="${body}
# zram swap: reclaim anonymous cold pages early and read them back one page at a time.
vm.swappiness = ${SWAPPINESS}
vm.page-cluster = ${PAGE_CLUSTER}"
    else
        say "  note:  no zram, so vm.swappiness and vm.page-cluster stay at kernel defaults"
    fi
    write_file "$conf" 0644 "$body"
    if ((APPLY)); then
        say "  apply: sysctl -p $conf"
        as_root sysctl -q -p "$conf"
    else
        say "  would: sysctl -p $conf"
    fi
}

step_thp() {
    say "[thp] transparent hugepages -> madvise (Debian defaults to always, which inflates RSS of small processes)"
    local knob=/sys/kernel/mm/transparent_hugepage/enabled
    if [[ ! -e $knob ]]; then
        say "  skip:  kernel exposes no transparent hugepage control"
        return 0
    fi
    write_file /etc/tmpfiles.d/90-registrarmonitor-thp.conf 0644 "# Managed by scripts/tune_vm.sh (registrar_monitor).
w $knob - - - - madvise"
    if [[ "$(cat "$knob")" != *"[madvise]"* ]]; then
        act_sh "echo madvise > $knob" "echo madvise > $knob"
    else
        say "  ok:    already madvise"
    fi
}

step_journald() {
    say "[journald] persistent, compressed, capped at ${JOURNAL_MAX_USE}, ${JOURNAL_RETENTION} retention"
    write_file /etc/systemd/journald.conf.d/90-registrarmonitor.conf 0644 "# Managed by scripts/tune_vm.sh (registrar_monitor).
[Journal]
Storage=persistent
Compress=yes
SystemMaxUse=${JOURNAL_MAX_USE}
SystemMaxFileSize=16M
MaxRetentionSec=${JOURNAL_RETENTION}
RateLimitIntervalSec=30s
RateLimitBurst=5000"
    if ((FILE_CHANGED)); then
        # Existing service log streams survive a journald restart.
        act "systemctl restart systemd-journald.service" systemctl restart systemd-journald.service
    fi
}

step_timers() {
    say "[timers] apt and fstrim maintenance at a fixed off-peak time (${TZ_NAME}); man-db timer off"
    local when changed=0
    if ! when=$(systemd-analyze calendar "*-*-* 03:30:00 ${TZ_NAME}" 2>&1); then
        say "  skip:  this systemd cannot parse a timezone in OnCalendar: ${when%%$'\n'*}"
        return 0
    fi
    say "  check: next apt-daily run would be $(printf '%s\n' "$when" | awk '/Next elapse/ {sub(/^ *Next elapse: */, ""); print; exit}')"

    local -a restart=()
    timer_dropin() { # unit calendar delay
        local unit=$1 calendar=$2 delay=$3
        if ! unit_exists "$unit"; then
            say "  skip:  $unit is not installed"
            return 0
        fi
        write_file "/etc/systemd/system/${unit}.d/90-registrarmonitor.conf" 0644 "# Managed by scripts/tune_vm.sh (registrar_monitor).
# Persistent=false: restarting the timer or booting never triggers an immediate catch-up run.
[Timer]
OnCalendar=
OnCalendar=${calendar} ${TZ_NAME}
RandomizedDelaySec=${delay}
Persistent=false"
        if ((FILE_CHANGED)); then
            changed=1
            restart+=("$unit")
        fi
    }
    timer_dropin apt-daily.timer '*-*-* 03:30:00' 20min
    timer_dropin apt-daily-upgrade.timer '*-*-* 04:00:00' 20min
    timer_dropin fstrim.timer 'Mon *-*-* 04:40:00' 10min
    if ((changed)); then
        NEED_RELOAD=1
        TIMERS_TO_RESTART=("${restart[@]}")
    fi

    if unit_exists man-db.timer && [[ "$(systemctl is-enabled man-db.timer 2>/dev/null || true)" == "enabled" ]]; then
        act "systemctl disable --now man-db.timer" systemctl disable --now man-db.timer
    else
        say "  ok:    man-db.timer not enabled"
    fi
}
TIMERS_TO_RESTART=()

step_delegation() {
    say "[delegation] let user@.service delegate '${DELEGATE_CONTROLLERS}' (needed by the build scope's MemoryMax/CPUWeight/IOWeight)"
    local uid list
    uid=$(id -u "$RUNTIME_USER" 2>/dev/null) || {
        say "  skip:  unknown RUNTIME_USER=$RUNTIME_USER"
        return 0
    }
    if list=$(controllers_of "$uid"); then
        say "  now:   user@${uid}.service delegates: $list"
    else
        say "  now:   user manager for $RUNTIME_USER is not running (cannot read delegation)"
    fi
    write_file /etc/systemd/system/user@.service.d/90-registrarmonitor-delegate.conf 0644 "# Managed by scripts/tune_vm.sh (registrar_monitor).
[Service]
Delegate=${DELEGATE_CONTROLLERS}"
    if ((FILE_CHANGED)); then
        NEED_RELOAD=1
        NOTES+=("delegation drop-in is read on the next start of user@${uid}.service: reboot, or run 'sudo systemctl restart user@${uid}.service' while no website build is running (it ends that user's running build scopes, not the system units)")
    fi
}

step_osconfig() {
    say "[osconfig] google-osconfig-agent (opt-in): frees RAM; GCP OS Config patch management and inventory stop"
    if ! unit_exists google-osconfig-agent.service; then
        say "  skip:  google-osconfig-agent.service is not installed"
        return 0
    fi
    if [[ "$(systemctl is-enabled google-osconfig-agent.service 2>/dev/null || true)" == "enabled" ]] ||
        [[ "$(systemctl is-active google-osconfig-agent.service 2>/dev/null || true)" == "active" ]]; then
        act "systemctl disable --now google-osconfig-agent.service" systemctl disable --now google-osconfig-agent.service
    else
        say "  ok:    already disabled"
    fi
}

step_bfq() {
    say "[bfq] I/O scheduler bfq (opt-in): makes ionice -c 3 and IOWeight effective for the build; adds some CPU cost"
    local f name wrote=0
    for f in /sys/block/sd*/queue/scheduler /sys/block/vd*/queue/scheduler /sys/block/nvme*/queue/scheduler; do
        [[ -e $f ]] || continue
        name=$(basename "$(dirname "$(dirname "$f")")")
        if [[ "$(cat "$f")" != *bfq* ]]; then
            say "  note:  bfq not offered by $name ($(cat "$f")); trying modprobe bfq"
            act "modprobe bfq" modprobe bfq
        fi
        if [[ "$(cat "$f")" == *"[bfq]"* ]]; then
            say "  ok:    $name already uses bfq"
        else
            act_sh "echo bfq > $f" "echo bfq > $f"
        fi
        wrote=1
    done
    if ((wrote == 0)); then
        say "  skip:  no sd/vd/nvme disk found"
        return 0
    fi
    write_file /etc/udev/rules.d/60-registrarmonitor-io-scheduler.rules 0644 '# Managed by scripts/tune_vm.sh (registrar_monitor).
ACTION=="add|change", SUBSYSTEM=="block", ENV{DEVTYPE}=="disk", KERNEL=="sd[a-z]|vd[a-z]|nvme[0-9]*n[0-9]*", ATTR{queue/scheduler}="bfq"'
    if ((FILE_CHANGED)); then
        act "udevadm control --reload" udevadm control --reload
    fi
}

# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

if ((APPLY)); then
    say "APPLY mode: changing this host (project units are not restarted)."
    if [[ ${EUID} -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        echo "sudo is required for --apply" >&2
        exit 1
    fi
else
    say "DRY RUN: nothing is changed. Review the plan, then re-run with --apply when authorized."
fi
say "Runtime user: $RUNTIME_USER"
say
say "Current state"
status_report
say
say "Plan"

step_enabled zram && step_zram
step_enabled sysctl && step_sysctl
step_enabled thp && step_thp
step_enabled journald && step_journald
step_enabled timers && step_timers
step_enabled delegation && step_delegation
if ((DISABLE_OSCONFIG)) && step_enabled osconfig; then step_osconfig; fi
if ((BFQ)) && step_enabled bfq; then step_bfq; fi

if ((NEED_RELOAD)); then
    act "systemctl daemon-reload" systemctl daemon-reload
    if ((${#TIMERS_TO_RESTART[@]})); then
        act "systemctl restart ${TIMERS_TO_RESTART[*]}" systemctl restart "${TIMERS_TO_RESTART[@]}"
    fi
fi

say
if ((APPLY)); then
    say "Done. Resulting state"
    status_report
else
    say "Dry run complete. Nothing was changed."
fi
if ((${#NOTES[@]})); then
    say
    say "Follow-ups"
    for n in "${NOTES[@]}"; do say "  - $n"; done
fi
