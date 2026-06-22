"""LangGraph orchestrator wiring agents into a single per-instrument pipeline.

  load_snapshots → microstructure → risk_agent → emit_card → persist_signal

Each node is a small adapter around the agent it calls. State flows through
the typed :class:`AgentState` Pydantic model. We keep nodes synchronous —
the deterministic core does no LLM calls, so async would only add complexity
without latency benefit.

Note: LangGraph's ``StateGraph`` natively supports Pydantic state. We only
re-export ``run_pipeline`` for the CLI to call without importing langgraph
directly elsewhere.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from langgraph.graph import END, StateGraph
import pytz

from .agents import microstructure_agent, risk_agent
from .agents.snapshot_io import load_literature, load_macro, load_micro
from .config import InstrumentConfig, SystemConfig
from .data.upstox import UpstoxClient
from .persistence.schemas import SignalSnapshot
from .persistence.store import Store
from .reporting.trade_card import format_trade_card
from .state import AgentState

log = logging.getLogger(__name__)

INSTRUMENT_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "instrument-data" / "nifty_banknifty.json"

IST = pytz.timezone("Asia/Kolkata")


def _resolve_futures_key(symbol: str) -> Optional[str]:
    """Look up the nearest-expiry futures instrument_key from nifty_banknifty.json.

    Returns None if the file is missing or the symbol has no active futures,
    so callers can fall back to the spot key gracefully.
    """
    if not INSTRUMENT_DATA_PATH.exists():
        log.warning("instrument-data/nifty_banknifty.json not found — futures key unavailable")
        return None
    try:
        with open(INSTRUMENT_DATA_PATH) as f:
            data: Dict[str, Any] = json.load(f)
        section = data.get(symbol.lower(), {})
        futures = section.get("futures", [])
        if not futures:
            return None
        now_ms = datetime.now().timestamp() * 1000
        active = [ft for ft in futures if ft.get("expiry", 0) > now_ms]
        if not active:
            return futures[0].get("instrument_key")
        active.sort(key=lambda ft: ft["expiry"])
        nearest = active[0]
        log.info(
            "Resolved %s futures key: %s (%s)",
            symbol, nearest.get("instrument_key"), nearest.get("trading_symbol"),
        )
        return nearest.get("instrument_key")
    except Exception:
        log.warning("Failed to resolve futures key for %s", symbol, exc_info=True)
        return None


def _node_load_snapshots(
    cfg: SystemConfig, instrument: InstrumentConfig, client: UpstoxClient
) -> Callable[[AgentState], AgentState]:
    def _node(state: AgentState) -> AgentState:
        state.macro = load_macro(cfg.paths.snapshots, cfg.cadences.macro_refresh_days)
        state.micro = load_micro(cfg.paths.snapshots, cfg.cadences.micro_refresh_days)
        state.literature = load_literature(cfg.paths.snapshots, cfg.cadences.literature_refresh_days)
        if state.macro is None:
            state.log("WARN: macro snapshot missing or stale — continuing with no-bias fallback.")

        state.futures_instrument_key = _resolve_futures_key(instrument.symbol)
        volume_key = state.futures_instrument_key or instrument.upstox_instrument_key
        state.expected_volume_daily = _fetch_rolling_avg_volume(
            client, volume_key, instrument.symbol, state.now_ist
        )
        return state

    return _node


def _node_microstructure(
    cfg: SystemConfig, instrument: InstrumentConfig, client: UpstoxClient
) -> Callable[[AgentState], AgentState]:
    def _node(state: AgentState) -> AgentState:
        try:
            daily_vol = state.expected_volume_daily or _static_daily_volume(instrument.symbol)
            expected_now = _prorate_volume(daily_vol, state.now_ist)

            score = microstructure_agent.run_microstructure(
                client=client,
                instrument_key=instrument.upstox_instrument_key,
                related_keys=instrument.related_keys,
                expected_volume_now=expected_now,
                is_event_day=False,
                now_ist=state.now_ist,
                instrument_label=instrument.symbol,
                expiry_weekday=instrument.expiry_weekday,
                expiry_cadence=instrument.expiry_cadence,
                futures_instrument_key=state.futures_instrument_key,
                wbi_history=state.wbi_history,
            )
            state.microstructure = score
            state.wbi_history = score.raw_data.get("wbi_history", state.wbi_history)
            state.log(
                f"Microstructure score: raw={score.raw_score:.2f} adjusted={score.adjusted_score:.2f} "
                f"direction={score.direction}"
            )
        except Exception as exc:  # pragma: no cover — surface live data errors
            state.fail(f"microstructure_failed: {exc}")
        return state

    return _node


def _node_risk(
    cfg: SystemConfig, instrument: InstrumentConfig, store: Store
) -> Callable[[AgentState], AgentState]:
    def _node(state: AgentState) -> AgentState:
        if state.microstructure is None:
            return state
        return risk_agent.evaluate(
            state=state,
            cfg=cfg,
            store=store,
            instrument_label=instrument.symbol,
            lot_size=instrument.lot_size,
        )

    return _node


def _node_persist_signal(store: Store) -> Callable[[AgentState], AgentState]:
    def _node(state: AgentState) -> AgentState:
        if state.microstructure is None:
            return state
        snap = SignalSnapshot(
            instrument=state.microstructure.instrument,
            timestamp=state.microstructure.timestamp,
            raw_score=state.microstructure.raw_score,
            adjusted_score=state.microstructure.adjusted_score,
            direction=state.microstructure.direction,
            layer_breakdown_json=json.dumps(state.microstructure.layer_notes),
            macro_regime=state.macro.regime if state.macro else None,
            macro_bias=(
                state.macro.nifty_directional_bias
                if state.macro and state.instrument.upper() == "NIFTY"
                else (state.macro.banknifty_directional_bias if state.macro else None)
            ),
        )
        store.insert_signal(snap)
        return state

    return _node


def _node_emit(cfg: SystemConfig) -> Callable[[AgentState], AgentState]:
    def _node(state: AgentState) -> AgentState:
        if state.trade_card is not None:
            rendered = format_trade_card(state.trade_card, state.microstructure)  # type: ignore[arg-type]
            (cfg.paths.reports / f"{datetime.utcnow():%Y-%m-%d-%H-%M}-{state.instrument}.txt").write_text(
                rendered
            )
            state.log(f"TradeCard emitted for {state.instrument}")
        elif state.risk_gate is not None and not state.risk_gate.passed:
            state.log(f"NoTrade {state.instrument}: {', '.join(state.risk_gate.reasons_rejected)}")
        return state

    return _node


def build_graph(
    cfg: SystemConfig, instrument: InstrumentConfig, client: UpstoxClient, store: Store
):
    """Compile a per-instrument LangGraph that returns a final :class:`AgentState`."""

    g = StateGraph(AgentState)
    g.add_node("load_snapshots", _node_load_snapshots(cfg, instrument, client))
    g.add_node("microstructure", _node_microstructure(cfg, instrument, client))
    g.add_node("risk", _node_risk(cfg, instrument, store))
    g.add_node("persist_signal", _node_persist_signal(store))
    g.add_node("emit", _node_emit(cfg))
    g.set_entry_point("load_snapshots")
    g.add_edge("load_snapshots", "microstructure")
    g.add_edge("microstructure", "risk")
    g.add_edge("risk", "persist_signal")
    g.add_edge("persist_signal", "emit")
    g.add_edge("emit", END)
    return g.compile()


def run_pipeline(
    cfg: SystemConfig, instrument: InstrumentConfig, client: UpstoxClient, store: Store
) -> AgentState:
    graph = build_graph(cfg, instrument, client, store)
    initial = AgentState(instrument=instrument.symbol, now_ist=datetime.now(IST).replace(tzinfo=None))
    final = graph.invoke(initial)
    if isinstance(final, dict):
        # LangGraph returns a dict view of the state in some versions
        final = AgentState(**final)
    return final


def _static_daily_volume(symbol: str) -> float:
    """Hardcoded fallback when historical data is unavailable.

    Calibrated for *futures* contracts (the primary data source when a futures
    key resolves). Spot-index "volume" numbers from NSE are notional and 5-10x
    higher, so the old 50M/25M baselines caused chronic RVOL-too-low.
    """

    return {"NIFTY": 10_000_000, "BANKNIFTY": 6_000_000}.get(symbol, 5_000_000)


def _prorate_volume(daily_volume: float, now_ist: datetime) -> float:
    """Pro-rate a full-day volume estimate to the current time of day.

    Uses a finer-grained curve that reflects the typical U-shaped intraday
    volume profile for Indian index futures: heavy first hour, light midday,
    heavy close.  The old step function over-estimated expected volume early
    in the session, making RVOL appear chronically low.
    """

    t = now_ist.time()
    hour_frac = t.hour + t.minute / 60.0

    if hour_frac < 9.25:
        frac = 0.0
    elif hour_frac < 9.5:
        frac = 0.05
    elif hour_frac < 10.0:
        frac = 0.15
    elif hour_frac < 10.5:
        frac = 0.25
    elif hour_frac < 11.0:
        frac = 0.33
    elif hour_frac < 12.0:
        frac = 0.45
    elif hour_frac < 13.0:
        frac = 0.55
    elif hour_frac < 14.0:
        frac = 0.68
    elif hour_frac < 14.5:
        frac = 0.78
    elif hour_frac < 15.0:
        frac = 0.88
    else:
        frac = 1.0
    return daily_volume * frac


def _fetch_rolling_avg_volume(
    client: UpstoxClient,
    instrument_key: str,
    symbol: str,
    now_ist: datetime,
    lookback_days: int = 5,
) -> float:
    """Compute 5-day rolling average daily volume from Upstox historical data.

    Called once per pipeline invocation (in load_snapshots, not the hot path).
    Falls back to the static baseline on any failure so the pipeline never breaks.
    """

    to_date = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
    from_date = (now_ist - timedelta(days=lookback_days + 7)).strftime("%Y-%m-%d")
    try:
        hist = client.historical_candles(instrument_key, "1d", to_date, from_date)
        raw = hist.get("data", {}).get("candles", []) or []
        if len(raw) < 2:
            log.warning("Too few historical candles for RVOL baseline — using static fallback")
            return _static_daily_volume(symbol)
        recent = raw[:lookback_days]
        return sum(float(c[5]) for c in recent) / len(recent)
    except Exception:
        log.warning("Failed to fetch historical volume — using static fallback", exc_info=True)
        return _static_daily_volume(symbol)
