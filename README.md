# Claude Code spend tracker

Every day at 8:45am (macOS): records Claude Code cost per day and session, rebuilds `report.html`, and sends a notification.

Requires macOS and Node.js.

- Install: `./setup.sh` (backfill: `./setup.sh 2026-08-01`)
- View report: `open report.html`
- Uninstall: `launchctl bootout gui/$(id -u)/com.$(id -un).claude-spend-tracker`

## Hourly report

`./install_hourly.sh N` (N = 1-24) rebuilds `report_hourly.html` every N hours with today's cost per completed hour (the hour in progress is left out).
Runs once at install, then every N hours. Uses `ccost` (via npx) because ccusage has no hourly output; its totals can differ from the daily report.
Only today is shown, so hours after the last run of a day are not in any report.
Uninstall: `launchctl bootout gui/$(id -u)/com.$(id -un).claude-spend-tracker-hourly`

Costs are estimates (token counts at list prices), not your bill.
No notification? Enable "Script Editor" in System Settings → Notifications.
