# Claude Code spend tracker

Every day at 8:45am (macOS): records Claude Code cost per day and session, rebuilds `report.html`, and sends a notification.

Requires macOS and Node.js.

- Install: `./setup.sh` (backfill: `./setup.sh 2026-08-01`)
- View report: `open report.html`
- Uninstall: `launchctl bootout gui/$(id -u)/com.$(id -un).claude-spend-tracker`

## Spend projections

The current-month chart projects month-end spend from how much you work on each weekday. `.weights.json` sets that share per day, as a number from 0 (no work) to 1 (full day):

```json
{"mon": 1.0, "tue": 1.0, "wed": 1.0, "thu": 1.0, "fri": 0.75, "sat": 0.5, "sun": 0}
```

- `./setup.sh` creates the file once with Mon-Fri 1.0 and Sat/Sun 0, and keeps your edits on reruns. Edits apply at the next run.
- All seven keys are required. A missing file, missing or unknown key, negative or non-number value, or all-zero weights stops the run with an error.
- Weekly projection: cost per full day over the last 7 completed days (spend divided by weights), times each remaining day's weight.
- Daily projection: the latest completed day's cost divided by its weight, times the average weight, added to every remaining day.
- Days with weight 0 are left out of both rates. If no completed day this month has a weight above 0, the chart shows "No completed working days yet."

## Hourly report

`./install_hourly.sh N` (N = 1-24) rebuilds `report_hourly.html` every N hours with today's cost per completed hour (the hour in progress is left out).
Runs once at install, then every N hours. Uses `ccost` (via npx) because ccusage has no hourly output; its totals can differ from the daily report.
Only today is shown, so hours after the last run of a day are not in any report.
Uninstall: `launchctl bootout gui/$(id -u)/com.$(id -un).claude-spend-tracker-hourly`

Costs are estimates (token counts at list prices), not your bill.
No notification? Enable "Script Editor" in System Settings → Notifications.
