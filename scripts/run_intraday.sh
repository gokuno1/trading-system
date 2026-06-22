#!/usr/bin/env bash
# Cron suggestion: 25 9 * * 1-5 (09:25 IST, Mon-Fri)
set -euo pipefail
cd "$(dirname "$0")/.."
python -m trading_system.cli intraday
