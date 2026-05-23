# VPS Migration Guide

This guide moves an existing VPS deployment to the cleaned source layout where generated runtime files stay off Git and Telegram credentials live only in environment variables.

The VPS runtime state has priority. Treat the live VPS `data/` directory and database files as the source of truth, even when Git removes old tracked runtime files during the first pull of this cleanup.

## 1. Rotate Leaked Secrets

The previous `settings.toml` contained a Telegram bot token. Treat that token as compromised.

1. In BotFather, revoke the old token and generate a new token for the bot.
2. Put the new values in the VPS `.env` file:

```bash
TELEGRAM_BOT_TOKEN=replace_with_new_token
TELEGRAM_CHAT_ID=replace_with_chat_id
```

3. If this repository was public while the token was present, rewrite or purge the Git history before relying on repository secrecy.

## 2. Stop The Service

Stop the daemon before touching runtime files so SQLite databases and generated reports are copied from a quiet state:

```bash
sudo systemctl stop registrarmonitor || true
```

## 3. Back Up VPS Runtime State

Run this from the current VPS project directory before pulling the new version:

```bash
mkdir -p ~/registrar-monitor-backups
tar -czf ~/registrar-monitor-backups/registrar-runtime-$(date +%Y%m%d-%H%M%S).tar.gz data assets/downloads assets/changes logs .env 2>/dev/null || true
```

The cleaned repo removes generated files from version control, so the pull can delete previously tracked local runtime files. This backup is the copy that wins after the source update.

Optionally save the backup path for the restore commands:

```bash
RUNTIME_BACKUP=$(ls -t ~/registrar-monitor-backups/registrar-runtime-*.tar.gz | head -n1)
```

## 4. Update The Checkout

```bash
git fetch --all --prune
git status --short
git pull --ff-only
uv sync --locked
```

If `git pull --ff-only` refuses because old generated files were modified locally, make a fresh backup first. Then move only those runtime paths out of the checkout and retry:

```bash
mkdir -p ~/registrar-monitor-backups/pre-pull-runtime
mv data assets/downloads assets/changes logs ~/registrar-monitor-backups/pre-pull-runtime/ 2>/dev/null || true
git pull --ff-only
```

## 5. Restore VPS Runtime State

Restore the VPS runtime files after the source update. This makes the VPS database files authoritative over the cleaned Git tree:

```bash
mkdir -p data assets/downloads assets/changes logs
tar -xzf "$RUNTIME_BACKUP" --strip-components=0
```

If you used the fallback `pre-pull-runtime` directory instead of `RUNTIME_BACKUP`, restore from it:

```bash
cp -a ~/registrar-monitor-backups/pre-pull-runtime/data/. data/ 2>/dev/null || true
cp -a ~/registrar-monitor-backups/pre-pull-runtime/downloads/. assets/downloads/ 2>/dev/null || true
cp -a ~/registrar-monitor-backups/pre-pull-runtime/changes/. assets/changes/ 2>/dev/null || true
cp -a ~/registrar-monitor-backups/pre-pull-runtime/logs/. logs/ 2>/dev/null || true
```

## 6. Install Environment File

Create or update `.env` at the project root:

```bash
TELEGRAM_BOT_TOKEN=replace_with_new_token
TELEGRAM_CHAT_ID=replace_with_chat_id
```

Keep `.env` mode restricted:

```bash
chmod 600 .env
```

## 7. Install Systemd Service

Run the setup script from the project root:

```bash
bash scripts/setup_vps.sh
```

Then install the generated service:

```bash
sudo cp scripts/registrarmonitor.service /etc/systemd/system/registrarmonitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now registrarmonitor
sudo systemctl status registrarmonitor
```

Follow logs with:

```bash
sudo journalctl -u registrarmonitor -f
```

## 8. Smoke Test

```bash
uv run monitor report --no-telegram
uv run monitor db stats
```

For a real Telegram delivery test, run:

```bash
uv run monitor report --stateful
```

## 9. Website Deployment

The website package is under `assets/website`.

```bash
cd assets/website
npm ci
npm run build
```

For Cloudflare deployment, configure Wrangler credentials in the VPS environment and run:

```bash
uv run monitor deploy --deploy
```
