# Architecture

## Layered decision model

```
┌─────────────────────────────────────────────────────────────────┐
│  Strategic layer (slow cadence, LLM-driven via Cursor skills)   │
│                                                                 │
│  macro-research-analyst   →  MacroSnapshot (weekly)             │
│  micro-research-analyst   →  MicroSnapshot (weekly)             │
│  openalex-high-impact     →  LiteratureSnapshot (monthly)       │
│                                                                 │
└────────────────┬────────────────────────────────────────────────┘
                 │ JSON snapshots in data/snapshots/
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tactical layer (every 5 min, deterministic Python)             │
│                                                                 │
│  microstructure_agent  ←  Upstox HTTP API                       │
│       │                                                         │
│       ▼                                                         │
│  score_aggregator (7 layers from skill spec)                    │
│       │                                                         │
│       ▼                                                         │
│  risk_agent (gates: time, daily P&L, concurrency,               │
│              macro alignment, R:R, sizing)                      │
│       │                                                         │
│       ▼                                                         │
│  TradeCard ──→ data/reports/  +  SQLite signals + paper trades  │
└─────────────────────────────────────────────────────────────────┘
```

## Why two layers, not one

Macro/micro/literature analysis is **expensive, qualitative, and slow** — it needs WebSearch, multi-pass coverage gates, and synthesis. Running it inside an intraday hot loop would burn tokens and add unbounded latency. Microstructure analysis is **cheap, quantitative, and fast** — order book and option chain math runs in milliseconds against a live HTTP API.

By snapshotting the strategic layer to disk and only re-running it on its natural cadence (weekly / monthly), the tactical layer can spin up every 5 minutes against deterministic Python with no LLM in the loop. This is also what makes the intraday loop robust enough to run as a cron job.

## State flow (LangGraph)

```
load_snapshots → microstructure → risk → persist_signal → emit
```

- `AgentState` is a single Pydantic model that flows through every node.
- Each node is a small function that mutates one or two fields on the state.
- Failures (e.g. Upstox 5xx) are recorded into `state.errors` rather than raised — the loop should continue, not crash the daemon.

## Skill mapping (4 skills → modules)

| Skill | Module(s) |
|---|---|
| `market-microstructure-analyst` (Phases 1–4) | `analysis/option_math.py`, `analysis/microstructure.py`, `analysis/score_aggregator.py`, `agents/microstructure_agent.py` |
| `market-microstructure-analyst` (Phase 4 risk) | `risk/sizer.py`, `risk/stop_logic.py`, `risk/pnl_tracker.py`, `agents/risk_agent.py` |
| `market-microstructure-analyst` (Phase 5 output) | `reporting/trade_card.py`, `reporting/daily_report.py` |
| `macro-research-analyst` | `agents/macro_agent.py` (prompt) + `state.MacroSnapshot` (typed output) |
| `micro-research-analyst` | `agents/micro_agent.py` (prompt) + `state.MicroSnapshot` |
| `openalex-high-impact-research` | `agents/research_agent.py` (prompt) + `state.LiteratureSnapshot` |

The macro/micro/research skills are not re-implemented — the system **invokes the existing skills** via Cursor agent sessions and consumes their structured JSON output. Updating those skills automatically improves the trading system without code changes here.

## Risk model

Hard caps applied **in order** in `agents/risk_agent.py`:

1. Session-time gate (no entries before 09:20 IST or after 15:10 IST).
2. Daily-loss gate (3% of capital realized + unrealized).
3. Concurrent-position gate (max 3).
4. Microstructure score gate (|adjusted| ≥ 5 per skill spec).
5. Macro-alignment gate (penalty applied; configurable hard reject).
6. Stop/target gate (R:R ≥ 1.5:1; underlying stop ≤ 2× ATR).
7. Position-sizing gate (per-trade risk ≤ 1%, notional ≤ 10%, premium ≤ 1.5%).

Any gate failing means **no trade card is emitted**. The system never overrides a hard cap based on conviction — conviction only modulates sizing *within* the cap.

## Data dependencies

- **Upstox** (HTTP, direct):
  - `/v2/option/chain` — chain payload (PCR, max pain, OI walls, IV skew)
  - `/v2/market-quote/quotes` — full quote with depth
  - `/v2/historical-candle/intraday/<key>/5minute` — session candles for VWAP, ATR, delta proxy
  - `/v2/market-quote/ohlc` — for related-instrument cross-market alignment
- **Cursor MCPs** (skill-driven, agent runtime):
  - `OpenAlex_Research_1_0_0` — literature workflow
  - `upstox-optionchain` — same as above but accessed via MCP for ad-hoc analysis
- **Persistence**:
  - SQLite (default, in `data/ledger/ledger.sqlite`)
  - MongoDB-ready interface (drop-in once the MCP is restored)

## Why Python + LangGraph + Pydantic

- **Python** — every quant data tool (numpy/pandas/scipy) is here; the existing repo is already Python (`predict.py`, `train.py`).
- **LangGraph** — explicit, typed, replayable agent graphs. Lets us add nodes (e.g. an LLM-assisted score override) later without changing call sites.
- **Pydantic** — typed AgentState catches integration bugs at construction time. Same models round-trip to MongoDB later.
- **Typer** — clean CLI, no boilerplate, plays well with cron.

## What's deliberately not here (Phase 2+)

- Live order placement (Upstox `place_order` tool exists in the MCP and a `place_order_sample` shim — left untouched on purpose).
- Multi-leg strategies (debit spreads, iron condors). With small capital and signal-only mode, ATM long-only is sufficient.
- Backtesting harness. The existing repo's `predict.py`/`train.py` already provide a directional-prediction baseline; integrate them as a 6th layer score later if desired.
- Equity swing trades on the NIFTY 50 stock universe. Out of scope per the user's instrument selection.
