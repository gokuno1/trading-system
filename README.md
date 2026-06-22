# Agentic Algorithmic Trading System

Combines four Cursor skills into one signal-generation pipeline for **NIFTY / BANKNIFTY weekly options**:

| Skill | Cadence | Role |
|---|---|---|
| `macro-research-analyst` | weekly | Sets directional bias (regime → NIFTY/BANKNIFTY tilt) |
| `micro-research-analyst` | weekly | Heavyweight quality (sector pull/drag) |
| `openalex-high-impact-research` | monthly | Edge validation against published literature |
| `market-microstructure-analyst` | every 5 min during session | Live trade card generation |

The deterministic core (this Python package) handles scoring math, risk gates, position sizing, the paper-trade engine, and persistence. The three research skills run as Cursor agent invocations and write JSON snapshots that the core consumes.

> **Phase 1 only** — emits trade cards and runs a paper-trade engine. There is **no live order placement**. See `docs/RUNBOOK.md` for the cron schedule and `docs/ARCHITECTURE.md` for the design rationale.

## Quick start

```bash
cd trading-system
pip install -e .                            # installs trading-system + langgraph
export UPSTOX_ACCESS_TOKEN=<token>          # same token used by the upstox MCP
trading-system bootstrap-snapshots          # writes example macro/micro/literature JSON
trading-system intraday-once                # one full pass; emits a trade card or no-trade reason
```

## Instrument data

Before running the trading system, filter the Upstox instrument master to extract NIFTY 50 and BANKNIFTY instrument keys:

```bash
python data/instrument-data/filter_instruments.py
```

This reads `NSE.json` (full 88K instrument master) and outputs `nifty_banknifty.json` with only the relevant index, futures, and options instruments. Re-run after downloading a fresh `NSE.json` or at monthly futures expiry rollover.

## Daily flow

1. **Sunday (offline)** — operator runs the OpenAlex literature prompt:
   ```
   trading-system print-prompt research
   ```
   pastes it into a Cursor agent session; the agent saves a snapshot to `data/snapshots/literature/`.
2. **Mon–Fri 08:30 IST** — operator (or cron) runs the macro + micro prompts in a Cursor agent session if their snapshots are stale. The agent saves snapshots into `data/snapshots/macro/` and `data/snapshots/micro/`.
3. **Mon–Fri 09:25 IST** — `trading-system intraday` loop starts. Every 5 minutes per instrument:
   - Fetch fresh option chain + intraday candles + L2 depth from Upstox.
   - Compute the 7-layer microstructure score.
   - Apply risk gates (session time, daily loss, concurrency, R:R, sizing).
   - Emit a trade card to `data/reports/<timestamp>-<instrument>.txt` if all gates pass.
   - Persist signal + score history to SQLite.
4. **Mon–Fri 15:30 IST** — `trading-system eod` flattens any open paper trades and writes the daily report.

## What the system *will not* do

- It will not place live orders. Trade cards are written to disk for the operator to act on (or ignore).
- It will not chase setups in low-RVOL sessions, in the opening 5 minutes, or after 15:10 IST.
- It will not size when one lot's risk exceeds the per-trade budget — at ₹3 lakh capital with a 50-pt stop on a NIFTY option, this is a *frequent* outcome and is intentional.
- It will not bypass the daily-loss circuit breaker.

## Testing

```bash
pip install -e ".[dev]"
pytest -q
```

Three smoke tests cover scoring, sizing, and option-chain math.

## Files

- `src/trading_system/` — package
  - `analysis/` — scoring, option chain math, microstructure math (pure Python, unit-tested)
  - `agents/` — macro/micro/research prompt builders + microstructure agent + risk agent
  - `risk/` — sizer, stop logic, daily-PnL gate
  - `paper_trade/` — engine + ledger
  - `persistence/` — SQLite store with Mongo-ready interface
  - `reporting/` — trade card and daily report renderers
  - `orchestrator.py` — LangGraph pipeline wiring
  - `cli.py` — Typer CLI
- `config/` — three YAMLs: system, risk, instruments
- `data/` — runtime artifacts (snapshots, ledger DB, reports)
- `docs/` — architecture + runbook
- `tests/` — smoke tests
