# Claude Code spend tracker

Every day at 8:45am (macOS): records Claude Code cost per day and session, rebuilds `report.html`, and sends a notification.

Requires macOS and Node.js.

- Install: `./setup.sh` (backfill: `./setup.sh 2026-08-01`)
- View report: `open report.html`
- Uninstall: `launchctl bootout gui/$(id -u)/com.$(id -un).claude-spend-tracker`

Costs are estimates (token counts at list prices), not your bill.
No notification? Enable "Script Editor" in System Settings → Notifications.
