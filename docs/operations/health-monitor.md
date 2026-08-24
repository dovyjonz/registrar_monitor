# Service health monitor

The independent `registrarmonitor-health.service` checks these two canonical
units every 30 seconds:

- `registrarmonitor.service` — scheduler, reports, and dashboard publication
- `registrarmonitor-bot.service` — private Telegram subscriptions and delivery

When either unit changes from active to another systemd state, the health
monitor sends one alert to the positive numeric `TELEGRAM_BOT_TEST_USER_ID`
using `TELEGRAM_BOT_TOKEN`. It does not poll Telegram, change either main unit,
or inspect user identifiers. A single recovery message is sent when both units
become active again. Telegram delivery failures are retried on the next check.

The generated unit is created by `scripts/setup_vps.sh`:

```bash
sudo cp scripts/registrarmonitor-health.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Installation, enablement, and startup remain separate operator-authorized
actions. The health monitor requires the existing `.env` values for both the bot
token and test operator ID.
