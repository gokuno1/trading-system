"""Microstructure agent — fully deterministic.

This agent fetches live data from Upstox, runs the 8-layer scoring math,
and returns a populated :class:`MicrostructureScore`. It is the only agent
that does I/O on the hot path. The macro/micro/research agents are
snapshot-driven and contribute via :class:`AgentState`.

The agent is deliberately small — it composes the analysis modules rather
than re-implementing them. Most of the cleverness lives in
``analysis/option_math.py`` and ``analysis/score_aggregator.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

from ..analysis.microstructure import (
    Candle,
    atr,
    cumulative_delta_proxy,
    depth_imbalance_l1,
    detect_walls,
    parse_candles,
    rvol,
    session_high_low,
    weighted_book_imbalance,
)
from ..analysis.option_math import compute_features
from ..analysis.score_aggregator import LayerInputs, aggregate
from ..data.upstox import UpstoxClient
from ..state import MicrostructureScore


def _depth_levels(quote: Dict) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Extract 5-level depth from a Upstox full-quote payload.

    The exact JSON shape can vary by index/equity. We defensively probe the
    common keys and fall back to empty lists; downstream layers degrade
    gracefully when depth is missing.
    """

    bids: List[Tuple[float, float]] = []
    asks: List[Tuple[float, float]] = []
    for key in ("depth", "marketDepth", "market_depth"):
        d = quote.get(key)
        if not d:
            continue
        b = d.get("buy") or d.get("bids") or []
        a = d.get("sell") or d.get("asks") or []
        for lvl in b[:5]:
            bids.append((float(lvl.get("price", 0)), float(lvl.get("quantity", 0))))
        for lvl in a[:5]:
            asks.append((float(lvl.get("price", 0)), float(lvl.get("quantity", 0))))
        if bids or asks:
            break
    return bids, asks


def _related_alignment(
    main_pct: float, related_pct_changes: Dict[str, float]
) -> float:
    """Return [-1, 1] alignment: +1 = all related move with main, -1 = all opposite."""

    if not related_pct_changes:
        return 0.0
    direction = 1.0 if main_pct >= 0 else -1.0
    matches = 0
    for v in related_pct_changes.values():
        rel_dir = 1.0 if v >= 0 else -1.0
        matches += 1 if rel_dir == direction else -1
    return matches / len(related_pct_changes)


def _classify_session_regime(candles: List[Candle], session_atr: float) -> str:
    if len(candles) < 6:
        return "range"
    closes = [c.close for c in candles]
    n = len(closes)
    higher_highs = sum(1 for i in range(1, n) if candles[i].high > candles[i - 1].high)
    lower_lows = sum(1 for i in range(1, n) if candles[i].low < candles[i - 1].low)
    if higher_highs > n * 0.6:
        return "trending_up"
    if lower_lows > n * 0.6:
        return "trending_down"
    range_pct = (max(c.high for c in candles) - min(c.low for c in candles)) / closes[0]
    if range_pct < 0.002:
        return "drift"
    if session_atr / closes[0] > 0.005:
        return "volatile"
    return "range"


def run_microstructure(
    client: UpstoxClient,
    instrument_key: str,
    related_keys: List[str],
    expected_volume_now: float,
    is_event_day: bool,
    now_ist: datetime,
    instrument_label: str,
    expiry_weekday: str = "Tuesday",
    expiry_cadence: str = "weekly",
    futures_instrument_key: str | None = None,
    wbi_history: List[float] | None = None,
) -> MicrostructureScore:
    """Fetch live data and compute the layered score.

    When ``futures_instrument_key`` is provided, L1 (order book), L2 (delta),
    and L3 (RVOL) are computed from the futures contract which has real depth,
    tick-level volume, and a tradeable order book — unlike the spot index.
    Options (L4) and price reference remain on the spot key.
    """

    # --- Spot data: option chain (L4), price reference ---
    chain = client.option_chain(
        instrument_key, expiry_weekday=expiry_weekday, expiry_cadence=expiry_cadence
    )
    of = compute_features(chain)
    spot = of.spot

    # --- Futures data: order book (L1), candles for delta/RVOL/VWAP (L2-L5, L7-L8) ---
    # When a futures key is provided, use it for ALL candle + depth data.
    # Futures have real depth, tick-level volume, and track spot within ~0.1% basis.
    # This avoids a second API call for spot candles and produces better data.
    candle_key = futures_instrument_key or instrument_key
    depth_key = futures_instrument_key or instrument_key

    quote = client.full_quote([depth_key])
    main_quote = next(iter(quote.get("data", {}).values()), {})
    bids, asks = _depth_levels(main_quote)

    intraday = client.intraday_candles(candle_key, "5minute")
    candles = parse_candles(intraday)

    session_high, session_low = session_high_low(candles)
    last_close = candles[-1].close if candles else spot

    delta_series = cumulative_delta_proxy(candles)
    delta_trend = 0.0
    if len(delta_series) >= 6:
        recent = sum(delta_series[-3:]) / 3
        early = sum(delta_series[:3]) / 3
        delta_trend = 1.0 if recent > early else -1.0 if recent < early else 0.0

    session_volume = sum(c.volume for c in candles)
    rv = rvol(session_volume, expected_volume_now)
    session_atr = atr(candles, period=14)

    l1_imb = (
        depth_imbalance_l1(bids[0][1], asks[0][1]) if bids and asks else 0.0
    )
    wbi = weighted_book_imbalance(bids, asks) if bids and asks else 0.0

    _wbi_hist = list(wbi_history or [])
    _wbi_hist.append(wbi)
    _wbi_hist = _wbi_hist[-3:]

    walls = detect_walls(bids, asks)
    has_bid_wall = "bid" in walls
    has_ask_wall = "ask" in walls

    bid_depth = sum(q for _, q in bids)
    ask_depth = sum(q for _, q in asks)
    depth_ratio = (bid_depth / ask_depth) if ask_depth > 0 else 1.0

    spread_bps = 0.0
    if bids and asks:
        mid = (bids[0][0] + asks[0][0]) / 2
        spread_bps = ((asks[0][0] - bids[0][0]) / mid) * 10000 if mid > 0 else 0.0

    related_pct: Dict[str, float] = {}
    if related_keys:
        for rk in related_keys:
            try:
                rel_intra = client.intraday_candles(rk, "5minute")
                rel_candles = parse_candles(rel_intra)
                if rel_candles and len(rel_candles) >= 2:
                    rel_open = rel_candles[0].open
                    rel_close = rel_candles[-1].close
                    if rel_open > 0:
                        related_pct[rk] = (rel_close - rel_open) / rel_open * 100
            except Exception:
                pass
    main_pct = ((last_close - candles[0].open) / candles[0].open * 100) if candles else 0.0
    cross_align = _related_alignment(main_pct, related_pct)

    session_regime = _classify_session_regime(candles, session_atr)

    inputs = LayerInputs(
        l1_imbalance=l1_imb,
        weighted_imbalance=wbi,
        has_bid_wall_near=has_bid_wall,
        has_ask_wall_near=has_ask_wall,
        spread_bps=spread_bps,
        delta_trend=delta_trend,
        price_at_session_high=last_close >= session_high * 0.999,
        price_at_session_low=last_close <= session_low * 1.001,
        depth_ratio=depth_ratio,
        rvol=rv,
        option_features=of,
        candles=candles,
        last_price=last_close,
        related_alignment=cross_align,
        now_ist=now_ist.time(),
        is_event_day=is_event_day,
        session_regime=session_regime,
        wbi_history=_wbi_hist,
        intraday_move_pct=main_pct,
    )
    out = aggregate(inputs)

    return MicrostructureScore(
        instrument=instrument_label,
        timestamp=now_ist,
        layer1_book_structure=out["layer1"][0],
        layer2_order_flow=out["layer2"][0],
        layer3_liquidity=out["layer3"][0],
        layer4_option_chain=out["layer4"][0],
        layer5_vwap_volume=out["layer5"][0],
        layer6_cross_market=out["layer6"][0],
        layer7_context_multiplier=out["layer7_multiplier"][0],
        layer8_candle_momentum=out["layer8"][0],
        raw_score=out["raw_score"],
        adjusted_score=out["adjusted_score"],
        direction=out["direction"],
        layer_notes={
            "L1": out["layer1"][1],
            "L2": out["layer2"][1],
            "L3": out["layer3"][1],
            "L4": out["layer4"][1],
            "L5": out["layer5"][1],
            "L6": out["layer6"][1],
            "L7": out["layer7_multiplier"][1],
            "L8": out["layer8"][1],
        },
        raw_data={
            "spot": spot,
            "atr": session_atr,
            "rvol": rv,
            "session_high": session_high,
            "session_low": session_low,
            "vwap_proxy_close": last_close,
            "session_regime": session_regime,
            "atm_call_premium": next(
                (
                    (r.ce_bid + r.ce_ask) / 2 if (r.ce_bid and r.ce_ask) else r.ce_ltp
                    for r in of.strikes
                    if r.strike == of.atm_strike
                ),
                of.atm_straddle_price / 2,
            ),
            "atm_put_premium": next(
                (
                    (r.pe_bid + r.pe_ask) / 2 if (r.pe_bid and r.pe_ask) else r.pe_ltp
                    for r in of.strikes
                    if r.strike == of.atm_strike
                ),
                of.atm_straddle_price / 2,
            ),
            "option_features": {
                "atm_strike": of.atm_strike,
                "atm_iv": of.atm_iv,
                "atm_straddle_price": of.atm_straddle_price,
                "highest_call_oi_strike": of.highest_call_oi_strike,
                "highest_put_oi_strike": of.highest_put_oi_strike,
                "max_pain": of.max_pain,
                "pcr_oi": of.pcr_oi,
                "expiry": of.expiry,
            },
            "walls": walls,
            "wbi_history": _wbi_hist,
        },
    )
