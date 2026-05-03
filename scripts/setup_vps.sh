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

echo "Project root: $PROJECT_ROOT"
echo "Installing dependencies..."
uv sync

# Generate systemd service file dynamically
cat > scripts/registrarmonitor.service <<EOF
[Unit]
Description=Registrar Monitor Polling Daemon
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_ROOT
Environment="PATH=$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=$HOME/.cargo/bin/uv run monitor schedule
Restart=always
RestartSec=5
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=registrarmonitor

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "Setup complete."
echo "A systemd service file has been generated at scripts/registrarmonitor.service"
echo ""
echo "To install and start the background daemon, run:"
echo "  sudo cp scripts/registrarmonitor.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now registrarmonitor"
echo "  sudo journalctl -u registrarmonitor -f  # to view live logs"
