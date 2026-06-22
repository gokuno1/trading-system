#!/usr/bin/env bash
# Cron suggestion: 30 15 * * 1-5 (15:30 IST, Mon-Fri)
set -euo pipefail
cd "$(dirname "$0")/.."
python -m trading_system.cli eod
