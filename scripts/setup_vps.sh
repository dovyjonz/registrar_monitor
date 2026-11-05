#!/bin/bash
set -e

echo "Setting up Registrar Monitor on VPS..."

# Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# Navigate to project root
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

# Account the services run as. Defaults to the invoking user; set RUNTIME_USER
# when an operator runs this script for a different runtime account.
RUNTIME_USER="${RUNTIME_USER:-$USER}"
RUNTIME_HOME=$(getent passwd "$RUNTIME_USER" | cut -d: -f6)
if [ -z "$RUNTIME_HOME" ]; then
    echo "Error: could not find a home directory for RUNTIME_USER=$RUNTIME_USER" >&2
    exit 1
fi

RUNTIME_UID=$(id -u "$RUNTIME_USER")

# Resource limits (override from the environment when running this script).
# The website build runs in its own user-manager scope with its own cap, so the
# poller stays small; these caps keep a leak from taking the whole VM.
SCHEDULER_MEMORY_HIGH="${SCHEDULER_MEMORY_HIGH:-300M}"
SCHEDULER_MEMORY_MAX="${SCHEDULER_MEMORY_MAX:-400M}"
BOT_MEMORY_HIGH="${BOT_MEMORY_HIGH:-200M}"
BOT_MEMORY_MAX="${BOT_MEMORY_MAX:-300M}"
TUNNEL_MEMORY_HIGH="${TUNNEL_MEMORY_HIGH:-96M}"
TUNNEL_MEMORY_MAX="${TUNNEL_MEMORY_MAX:-128M}"
LITESTREAM_MEMORY_HIGH="${LITESTREAM_MEMORY_HIGH:-128M}"
LITESTREAM_MEMORY_MAX="${LITESTREAM_MEMORY_MAX:-192M}"
# V8 heap cap for the Node children (vite build, wrangler) the poller starts. It
# must stay well under website.build_memory_max (600M by default) so V8 collects
# garbage before the build scope's hard cap kills it.
NODE_MAX_OLD_SPACE_MB="${NODE_MAX_OLD_SPACE_MB:-384}"
# glibc keeps one malloc arena per thread; two are enough here and keep RSS small
# (the poller and bot use worker threads).
MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "Warning: .env was not found at $PROJECT_ROOT/.env"
    echo "Create it from .env.example and set TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_WEBHOOK_URL, and TELEGRAM_WEBHOOK_SECRET before enabling Telegram sends."
fi

echo "Project root: $PROJECT_ROOT"
echo "Installing dependencies..."
uv sync --locked

# The poller starts its website build with `systemd-run --user --scope`, which
# needs the runtime user's systemd manager (and /run/user/<uid>) to exist even
# when nobody is logged in.
if ! loginctl show-user "$RUNTIME_USER" --property=Linger 2>/dev/null | grep -q '=yes'; then
    if ! sudo -n loginctl enable-linger "$RUNTIME_USER" 2>/dev/null; then
        echo "Warning: could not enable lingering; run: sudo loginctl enable-linger $RUNTIME_USER" >&2
    fi
fi

echo "For a make-free, no-sync runtime diagnostic, run:"
echo "  ./scripts/runtime_doctor.sh"

# Generate systemd service file dynamically
cat > scripts/registrarmonitor.service <<EOF
[Unit]
Description=Registrar Monitor Polling Daemon
After=network.target

[Service]
Type=simple
User=$RUNTIME_USER
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=-$PROJECT_ROOT/.env
Environment="PATH=$RUNTIME_HOME/.local/bin:$RUNTIME_HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment=XDG_RUNTIME_DIR=/run/user/$RUNTIME_UID
Environment=MALLOC_ARENA_MAX=$MALLOC_ARENA_MAX
Environment=NODE_OPTIONS=--max-old-space-size=$NODE_MAX_OLD_SPACE_MB
ExecStart=$PROJECT_ROOT/.venv/bin/monitor schedule
MemoryHigh=$SCHEDULER_MEMORY_HIGH
MemoryMax=$SCHEDULER_MEMORY_MAX
OOMScoreAdjust=-500
CPUWeight=200
IOWeight=200
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=registrarmonitor

[Install]
WantedBy=multi-user.target
EOF

cat > scripts/registrarmonitor-bot.service <<EOF
[Unit]
Description=Registrar Monitor Telegram Subscription Bot
Wants=network-online.target registrarmonitor-tunnel.service
After=network-online.target registrarmonitor.service registrarmonitor-tunnel.service

[Service]
Type=simple
User=$RUNTIME_USER
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=-$PROJECT_ROOT/.env
Environment="PATH=$RUNTIME_HOME/.local/bin:$RUNTIME_HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment=MALLOC_ARENA_MAX=$MALLOC_ARENA_MAX
ExecStart=$PROJECT_ROOT/.venv/bin/monitor bot
MemoryHigh=$BOT_MEMORY_HIGH
MemoryMax=$BOT_MEMORY_MAX
OOMScoreAdjust=-200
CPUWeight=150
IOWeight=150
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=registrarmonitor-bot

[Install]
WantedBy=multi-user.target
EOF

cat > scripts/registrarmonitor-tunnel.service <<EOF
[Unit]
Description=Registrar Monitor Cloudflare Tunnel for the Telegram webhook
Wants=network-online.target
After=network-online.target
Before=registrarmonitor-bot.service

[Service]
Type=simple
User=$RUNTIME_USER
WorkingDirectory=$PROJECT_ROOT
Environment="PATH=$RUNTIME_HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# Go soft heap target, below MemoryHigh, so the runtime collects before throttling.
Environment=GOMEMLIMIT=80MiB
ExecStart=/usr/bin/env cloudflared tunnel --no-autoupdate --config $PROJECT_ROOT/scripts/cloudflared-config.yml run
MemoryHigh=$TUNNEL_MEMORY_HIGH
MemoryMax=$TUNNEL_MEMORY_MAX
CPUWeight=75
IOWeight=75
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=registrarmonitor-tunnel

[Install]
WantedBy=multi-user.target
EOF

cat > scripts/registrarmonitor-litestream.service <<EOF
[Unit]
Description=Registrar Monitor Litestream Replication to R2
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$RUNTIME_USER
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=-$PROJECT_ROOT/.env
# Go soft heap target, below MemoryHigh, so the runtime collects before throttling.
Environment=GOMEMLIMIT=96MiB
ExecStart=/usr/bin/litestream replicate -config $PROJECT_ROOT/scripts/litestream.yml
MemoryHigh=$LITESTREAM_MEMORY_HIGH
MemoryMax=$LITESTREAM_MEMORY_MAX
# Replication is background work: yield CPU and disk to the poller and bot, and be
# the first system service the kernel kills (it restarts and resumes from its WAL position).
Nice=10
CPUWeight=50
IOWeight=50
OOMScoreAdjust=200
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=registrarmonitor-litestream

[Install]
WantedBy=multi-user.target
EOF

cat > scripts/registrarmonitor-network-watchdog.service <<EOF
[Unit]
Description=Registrar Monitor Network Recovery Check
After=network-online.target

[Service]
Type=oneshot
ExecStart=$PROJECT_ROOT/scripts/network_watchdog.sh
TimeoutStartSec=30
EOF

cat > scripts/registrarmonitor-network-watchdog.timer <<EOF
[Unit]
Description=Run Registrar Monitor Network Recovery Check

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s
Persistent=true
Unit=registrarmonitor-network-watchdog.service

[Install]
WantedBy=timers.target
EOF

echo ""
echo "Setup complete."
echo "A systemd service file has been generated at scripts/registrarmonitor.service"
echo "The subscription bot unit is at scripts/registrarmonitor-bot.service"
echo "The webhook tunnel unit is at scripts/registrarmonitor-tunnel.service (configure scripts/cloudflared-config.yml from scripts/cloudflared-config.yml.template first)"
echo "The Litestream replication unit is at scripts/registrarmonitor-litestream.service (needs the litestream binary and R2 variables in .env)"
echo "The network watchdog units are at scripts/registrarmonitor-network-watchdog.service and scripts/registrarmonitor-network-watchdog.timer"
echo ""
echo "For OS-level tuning (zram swap, sysctls, journald, off-peak apt, cgroup delegation), see"
echo "scripts/tune_vm.sh (dry run by default) and the VM tuning section of docs/operations.md."
echo ""
echo "To install the generated units without activating them, run:"
echo "  sudo cp scripts/registrarmonitor.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-bot.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-tunnel.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-litestream.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-network-watchdog.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-network-watchdog.timer /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo ""
echo "This setup did not change any service's installed or active state."
echo "Installation and activation are separate operator-authorized operations."
echo "  sudo journalctl -u registrarmonitor -f  # to view live logs"
