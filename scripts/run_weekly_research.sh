#!/usr/bin/env bash
# Cron suggestion: 0 10 * * SUN  (Sunday 10:00 — outside trading hours)
# Prints the OpenAlex research prompt; operator runs it in a Cursor agent
# session and the agent saves the JSON snapshot to data/snapshots/literature/.
set -euo pipefail
cd "$(dirname "$0")/.."
python -m trading_system.cli print-prompt research
