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

echo ""
echo "Setup complete."
echo "A systemd service file has been generated at scripts/registrarmonitor.service"
echo ""
echo "To install the canonical unit without activating it, run:"
echo "  sudo cp scripts/registrarmonitor.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo ""
echo "Monitoring remains paused. Do not enable or start the service yet."
echo "Before any future activation, the planned data changes must be completed and explicitly verified by an operator."
echo "Activation is a separate manual operation and is not performed by this setup script."
echo "  sudo journalctl -u registrarmonitor -f  # to view live logs"
