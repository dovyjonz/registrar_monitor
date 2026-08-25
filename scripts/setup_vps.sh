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

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "Warning: .env was not found at $PROJECT_ROOT/.env"
    echo "Create it from .env.example and set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID before enabling Telegram sends."
fi

echo "Project root: $PROJECT_ROOT"
echo "Installing dependencies..."
uv sync --locked

echo "For a make-free, no-sync runtime diagnostic, run:"
echo "  ./scripts/runtime_doctor.sh"

# Generate systemd service file dynamically
cat > scripts/registrarmonitor.service <<EOF
[Unit]
Description=Registrar Monitor Polling Daemon
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=-$PROJECT_ROOT/.env
Environment="PATH=$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/usr/bin/env uv run monitor schedule
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
Wants=network-online.target
After=network-online.target registrarmonitor.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=-$PROJECT_ROOT/.env
Environment="PATH=$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/usr/bin/env uv run monitor bot
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=registrarmonitor-bot

[Install]
WantedBy=multi-user.target
EOF

cat > scripts/registrarmonitor-health.service <<EOF
[Unit]
Description=Registrar Monitor Service Health Monitor
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_ROOT
EnvironmentFile=-$PROJECT_ROOT/.env
Environment="PATH=$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/usr/bin/env uv run monitor health-monitor
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=registrarmonitor-health

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
echo "The service health unit is at scripts/registrarmonitor-health.service"
echo "The network watchdog units are at scripts/registrarmonitor-network-watchdog.service and scripts/registrarmonitor-network-watchdog.timer"
echo ""
echo "To install the generated units without activating them, run:"
echo "  sudo cp scripts/registrarmonitor.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-bot.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-health.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-network-watchdog.service /etc/systemd/system/"
echo "  sudo cp scripts/registrarmonitor-network-watchdog.timer /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo ""
echo "This setup did not change any service's installed or active state."
echo "Installation and activation are separate operator-authorized operations."
echo "  sudo journalctl -u registrarmonitor -f  # to view live logs"
