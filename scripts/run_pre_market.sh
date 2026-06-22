#!/usr/bin/env bash
# Cron suggestion: 30 8 * * 1-5 (08:30 IST, Mon-Fri)
set -euo pipefail
cd "$(dirname "$0")/.."
python -m trading_system.cli pre-market
