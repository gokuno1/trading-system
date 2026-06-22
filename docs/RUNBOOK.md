# Runbook

## One-time setup

```bash
cd trading-system
python -m venv .venv && source .venv/bin/activate
pip install -e .
export UPSTOX_ACCESS_TOKEN=<paste-the-same-token-the-MCP-uses>
trading-system bootstrap-snapshots         # writes example macro/micro/literature
trading-system status
```

The bootstrap step lets the intraday loop run before any LLM-generated snapshot exists. **Replace those examples with real LLM-generated snapshots before relying on the trade cards.**

## Weekly routine (Sunday)

1. Open Cursor in this folder.
2. Run literature refresh (in Cursor agent chat):
   ```
   trading-system print-prompt research
   ```
   Copy the printed prompt into a fresh Cursor agent session. The agent runs the openalex-high-impact-research skill and saves the snapshot. This validates the system's edges against new literature.
3. Run macro + micro:
   ```
   trading-system print-prompt macro
   trading-system print-prompt micro
   ```
   In each case the agent writes the snapshot.

## Daily routine (Monday–Friday)

| Time (IST) | Action |
|---|---|
| 08:30 | `trading-system pre-market` — verify snapshot freshness |
| 09:25 | `trading-system intraday` — loop starts (or cron) |
| 09:30–15:10 | Loop runs every 5 min. Trade cards land in `data/reports/`. |
| 15:10 | Loop refuses any new entries (session rule) |
| 15:20 | All paper positions force-flattened by `eod` step |
| 15:30 | `trading-system eod` — daily report into `data/reports/daily-*.txt` |

## Cron example

```cron
30 8  * * 1-5  cd /path/to/trading-system && ./scripts/run_pre_market.sh    >> logs/pre_market.log 2>&1
25 9  * * 1-5  cd /path/to/trading-system && ./scripts/run_intraday.sh      >> logs/intraday.log   2>&1
30 15 * * 1-5  cd /path/to/trading-system && ./scripts/run_eod.sh           >> logs/eod.log        2>&1
0  10 * * SUN  cd /path/to/trading-system && ./scripts/run_weekly_research.sh >> logs/research.log 2>&1
```

## Operating discipline

- **Do NOT raise per-trade risk above 1%** to "make up" for missed setups. The math in `risk/sizer.py` is the only thing keeping small capital alive after a losing streak. At ₹3 lakh capital and 1% risk, a 5-trade losing streak draws down 5%. At 2% it draws down nearly 10% — that's months to recover from.
- **Trust the no-trade outcome.** The system frequently refuses to emit a card on small capital. That is the system working correctly. The micro-skill says it best: *"a great company at a terrible price is a bad trade"* — same logic for setups.
- **Re-bootstrap snapshots if a market regime breaks.** Macro snapshots are stale at 7 days; if a major event happens (Fed surprise, RBI emergency cut, India election), force a refresh by deleting yesterday's snapshot and running the macro prompt again.
- **Inspect the SQLite ledger**:
  ```bash
  sqlite3 data/ledger/ledger.sqlite '.schema'
  sqlite3 data/ledger/ledger.sqlite 'SELECT * FROM signals ORDER BY id DESC LIMIT 10;'
  sqlite3 data/ledger/ledger.sqlite 'SELECT * FROM trades WHERE status="OPEN";'
  ```

## Failure modes & responses

| Symptom | Cause | Response |
|---|---|---|
| `UpstoxError: UPSTOX_ACCESS_TOKEN not set` | env var missing | export the token (same one used by the MCP) |
| Empty option chain | Token expired | refresh token via Upstox login flow, update env |
| `microstructure_failed: HTTPStatusError 401` | Token expired mid-session | restart loop with fresh token |
| `microstructure_score_below_threshold` repeatedly | Setup conditions absent — this is normal | leave the daemon alone; do not lower the threshold |
| `One lot risks ₹X exceeds budget` | Capital too small for current premium / stop | accept it; do not bypass; consider sizing up capital |
| Daily loss circuit breaker | 3% loss reached for the day | the gate is doing its job — stop trading for the day |

## Migration to live trading (Phase 2 — out of current scope)

When you're ready:
1. Wire `paper_trade/engine.py` to the Upstox `/v2/order/place` endpoint behind a `cfg.environment == "live"` switch.
2. Add an explicit per-trade approval step (HTTP webhook to Slack/Telegram) — recommended.
3. Add slippage modelling (subtract 0.5–1.0 pt per leg from expected fill).
4. Add a kill switch that's reachable from outside the daemon (e.g., touch a `KILL` file).
