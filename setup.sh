#!/usr/bin/env bash
# One-step setup: checks prerequisites, creates the venv, runs tests and a first
# collection, then installs the daily 8:45am job.
# Usage: ./setup.sh [YYYY-MM-DD]   (optional: backfill from that date)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

[ "$(uname)" = "Darwin" ] || { echo "macOS only (uses launchd and osascript)" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
command -v npx >/dev/null || { echo "npx not found; install Node.js first" >&2; exit 1; }
[ -d "$HOME/.claude/projects" ] || { echo "no ~/.claude/projects; run Claude Code at least once" >&2; exit 1; }

[ -x .venv/bin/python3 ] || python3 -m venv .venv
.venv/bin/python3 -m unittest

# Backfill date is only for this first run; the daily job collects from the start of the month.
.venv/bin/python3 spend_tracker.py "$@"

./install.sh
echo "Report: $DIR/report.html"
