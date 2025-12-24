#!/bin/bash
# Setup script for registrar monitor stateful reporter cron jobs
# This script helps configure cron jobs to run the stateful reporter at :15 and :45 minutes

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Check if uv is available
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed or not in PATH"
    echo "Please install uv: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# Check if the project exists
if [ ! -f "$PROJECT_DIR/pyproject.toml" ]; then
    echo "Error: Could not find pyproject.toml in $PROJECT_DIR"
    echo "Make sure you're running this script from the registrar monitor project"
    exit 1
fi

echo "Registrar Monitor Cron Setup"
echo "============================="
echo "Project directory: $PROJECT_DIR"
echo ""

# Test the reporter first
echo "Testing stateful reporter..."
cd "$PROJECT_DIR"
if uv run monitor report --stateful --debug > /dev/null 2>&1; then
    echo "✅ Stateful reporter test successful"
else
    echo "❌ Stateful reporter test failed"
    echo "Please check your configuration and try again"
    exit 1
fi

# Create the cron entries
CRON_ENTRY_15="15 * * * * cd $PROJECT_DIR && /usr/bin/env uv run monitor report --stateful >> $PROJECT_DIR/cron_reporter.log 2>&1"
CRON_ENTRY_45="45 * * * * cd $PROJECT_DIR && /usr/bin/env uv run monitor report --stateful >> $PROJECT_DIR/cron_reporter.log 2>&1"

echo ""
echo "Cron entries to be added:"
echo "------------------------"
echo "$CRON_ENTRY_15"
echo "$CRON_ENTRY_45"
echo ""

# Check if entries already exist
EXISTING_CRON=$(crontab -l 2>/dev/null || true)
if echo "$EXISTING_CRON" | grep -q "monitor report --stateful"; then
    echo "⚠️  Warning: Found existing registrar monitor cron entries"
    echo "Existing entries:"
    echo "$EXISTING_CRON" | grep "monitor report --stateful" || true
    echo ""
    read -p "Do you want to remove existing entries and add new ones? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Setup cancelled"
        exit 0
    fi

    # Remove existing entries
    echo "Removing existing entries..."
    NEW_CRON=$(echo "$EXISTING_CRON" | grep -v "monitor report --stateful" || true)
else
    NEW_CRON="$EXISTING_CRON"
fi

# Add new entries
echo "Adding new cron entries..."
if [ -n "$NEW_CRON" ]; then
    UPDATED_CRON="$NEW_CRON"$'\n'"$CRON_ENTRY_15"$'\n'"$CRON_ENTRY_45"
else
    UPDATED_CRON="$CRON_ENTRY_15"$'\n'"$CRON_ENTRY_45"
fi

# Install the new crontab
echo "$UPDATED_CRON" | crontab -

echo "✅ Cron jobs installed successfully!"
echo ""
echo "The stateful reporter will now run at:"
echo "  - 15 minutes past each hour"
echo "  - 45 minutes past each hour"
echo ""
echo "Logs will be written to: $PROJECT_DIR/cron_reporter.log"
echo ""
echo "To verify installation:"
echo "  crontab -l | grep 'monitor report --stateful'"
echo ""
echo "To remove the cron jobs later:"
echo "  crontab -e"
echo "  (delete the lines containing 'monitor report --stateful')"
echo ""
echo "To monitor activity:"
echo "  tail -f $PROJECT_DIR/cron_reporter.log"
