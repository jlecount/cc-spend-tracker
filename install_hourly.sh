#!/usr/bin/env bash
# Installs (or reinstalls) a launchd job that rebuilds report_hourly.html every N hours. Idempotent.
# Usage: ./install_hourly.sh N   (N = 1..24)
set -euo pipefail

usage() { echo "usage: $0 N   (N = whole number of hours, 1-24)" >&2; exit 1; }
[ $# -eq 1 ] && [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 24 ] || usage
HOURS="$1"

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.$(id -un).claude-spend-tracker-hourly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
NODE_BIN_DIR="$(dirname "$(command -v npx)")"

[ -x "$DIR/.venv/bin/python3" ] || { echo "missing $DIR/.venv (python3 -m venv .venv)" >&2; exit 1; }

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python3</string>
    <string>$DIR/spend_hourly.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$NODE_BIN_DIR:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>$((HOURS * 3600))</integer>
  <key>StandardOutPath</key><string>$DIR/run_hourly.log</string>
  <key>StandardErrorPath</key><string>$DIR/run_hourly.log</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL (every $HOURS h, first run now); report: $DIR/report_hourly.html"
echo "uninstall: launchctl bootout gui/$(id -u)/$LABEL"
