#!/usr/bin/env bash
# Installs (or reinstalls) the daily 8:45 launchd job. Idempotent.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.$(id -un).claude-spend-tracker"
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
    <string>$DIR/spend_tracker.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$NODE_BIN_DIR:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>45</integer></dict>
  <key>StandardOutPath</key><string>$DIR/run.log</string>
  <key>StandardErrorPath</key><string>$DIR/run.log</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL; test now with: launchctl kickstart gui/$(id -u)/$LABEL"
