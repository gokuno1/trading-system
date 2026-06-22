"""Layered scoring per the market-microstructure-analyst skill.

Implements the 8-layer scoring rubric — layers 1–6 output a signal in
{-2, -1, 0, +1, +2}, layer 7 is a context multiplier in {0, 0.5, 0.75, 1.0},
and layer 8 scores consecutive-candle momentum (bypasses RVOL clamp).
The aggregator returns a ``MicrostructureScore`` for the agent state.

Each layer function takes already-computed deterministic inputs (no I/O)
so this module is a pure function of the data passed in. The microstructure
agent is responsible for fetching the data and calling these layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, Optional

from .microstructure import Candle, vwap, vwap_slope
from .option_math import OptionFeatures


@dataclass
class LayerInputs:
    # Order book (Layer 1)
    l1_imbalance: float
    weighted_imbalance: float
    has_bid_wall_near: bool
    has_ask_wall_near: bool
    spread_bps: float

    # Order flow proxy (Layer 2)
    delta_trend: float  # +1, 0, -1
    price_at_session_high: bool
    price_at_session_low: bool

    # Liquidity (Layer 3)
    depth_ratio: float  # bid_depth/ask_depth
    rvol: float

    # Option chain (Layer 4)
    option_features: OptionFeatures

    # VWAP / volume profile (Layer 5)
    candles: list  # List[Candle]
    last_price: float

    # Cross-market (Layer 6)
    related_alignment: float  # in [-1, 1]; +1 = all confirming, -1 = all contradicting

    # Context (Layer 7)
    now_ist: time
    is_event_day: bool
    session_regime: str  # "trending_up" | "trending_down" | "range" | "drift" | "volatile"

    # WBI history for L1 smoothing (rolling window of recent WBI readings)
    wbi_history: list = None  # type: ignore[assignment]  # List[float], populated by agent

    # Intraday momentum context (Layer 8 + regime-aware gating)
    intraday_move_pct: float = 0.0  # (last_price - day_open) / day_open * 100


def layer1_book_structure(x: LayerInputs) -> tuple[float, str]:
    history = x.wbi_history or []
    if history:
        smoothed_wbi = sum(history) / len(history)
    else:
        smoothed_wbi = x.weighted_imbalance

    wbi = smoothed_wbi
    l1 = x.l1_imbalance
    if wbi > 0.4 and l1 > 0.3 and not x.has_ask_wall_near:
        return 2.0, f"WBI strongly bullish (smoothed={wbi:.2f}), no overhead ask wall"
    if wbi > 0.2 or l1 > 0.3:
        return 1.0, f"Lean bullish book (smoothed WBI={wbi:.2f})"
    if wbi < -0.4 and l1 < -0.3 and not x.has_bid_wall_near:
        return -2.0, f"WBI strongly bearish (smoothed={wbi:.2f}), no support wall"
    if wbi < -0.2 or l1 < -0.3:
        return -1.0, f"Lean bearish book (smoothed WBI={wbi:.2f})"
    return 0.0, f"Balanced book (smoothed WBI={wbi:.2f})"


def layer2_order_flow(x: LayerInputs) -> tuple[float, str]:
    if x.price_at_session_high and x.delta_trend < 0:
        return -2.0, "Bearish divergence at session high"
    if x.price_at_session_low and x.delta_trend > 0:
        return 2.0, "Bullish divergence at session low"
    if x.delta_trend > 0:
        return 2.0 if not x.price_at_session_low else 1.0, "Cumulative delta rising"
    if x.delta_trend < 0:
        return -2.0 if not x.price_at_session_high else -1.0, "Cumulative delta falling"
    return 0.0, "No delta trend"


def layer3_liquidity(x: LayerInputs) -> tuple[float, str]:
    if x.rvol < 0.3:
        return 0.0, "RVOL too low — illiquid"
    dr = x.depth_ratio
    if dr > 1.5 and x.rvol > 1.2:
        return 2.0, f"Strong buy support, depth_ratio={dr:.2f}"
    if dr > 1.3 or (dr > 1.0 and x.rvol > 1.5):
        return 1.0, f"Lean bullish liquidity, depth_ratio={dr:.2f}"
    if dr < 0.67 and x.rvol > 1.2:
        return -2.0, f"Strong sell pressure, depth_ratio={dr:.2f}"
    if dr < 0.75 or (dr < 1.0 and x.rvol > 1.5):
        return -1.0, f"Lean bearish liquidity, depth_ratio={dr:.2f}"
    return 0.0, f"Balanced depth, depth_ratio={dr:.2f}"


def layer4_option_chain(x: LayerInputs) -> tuple[float, str]:
    of = x.option_features
    spot = of.spot
    pcr = of.pcr_oi
    if pcr > 1.2 and spot > of.max_pain and spot < of.highest_call_oi_strike:
        return 2.0, f"PCR={pcr:.2f}, spot above max_pain, room to call OI wall"
    if pcr > 1.0 and abs(spot - of.highest_put_oi_strike) / spot < 0.005:
        return 1.0, f"Spot resting on put OI support, PCR={pcr:.2f}"
    if pcr < 0.8 and spot < of.max_pain and spot > of.highest_put_oi_strike:
        return -1.0, f"PCR={pcr:.2f}, spot below max_pain"
    if pcr < 0.8 and abs(spot - of.highest_put_oi_strike) / spot < 0.003:
        return -2.0, f"Spot pressing on put OI support, cascade risk, PCR={pcr:.2f}"
    return 0.0, f"Range-bound options positioning, PCR={pcr:.2f}"


def layer5_vwap_volume(x: LayerInputs) -> tuple[float, str]:
    if not x.candles:
        return 0.0, "No candle data"
    vw = vwap(x.candles)
    slope = vwap_slope(x.candles)
    above = x.last_price > vw
    if above and slope > 0:
        return 2.0, f"Price above rising VWAP ({vw:.1f})"
    if above:
        return 1.0, f"Price above VWAP ({vw:.1f})"
    if (not above) and slope < 0:
        return -2.0, f"Price below falling VWAP ({vw:.1f})"
    if not above:
        return -1.0, f"Price below VWAP ({vw:.1f})"
    return 0.0, "Price at VWAP"


def layer6_cross_market(x: LayerInputs) -> tuple[float, str]:
    a = x.related_alignment
    strong_intraday = abs(x.intraday_move_pct) > 0.4

    if a >= 0.8:
        return 2.0, "All related instruments confirming"
    if a >= 0.4:
        return 1.0, "Mostly confirming"
    if a <= -0.8:
        if strong_intraday:
            return -1.0, "Cross-market contradiction (dampened by strong index move)"
        return -2.0, "Strong cross-market contradiction"
    if a <= -0.4:
        if strong_intraday:
            return 0.0, "Cross-market warning (overridden by strong index move)"
        return -1.0, "Some cross-market warning"
    return 0.0, "Mixed cross-market"


def layer7_multiplier(x: LayerInputs) -> tuple[float, str]:
    t = x.now_ist
    if x.session_regime == "drift":
        return 0.25, "Drift session — heavily dampened (floor 0.25)"
    if x.is_event_day:
        return 0.5, "Event day pre-event uncertainty"

    trending = x.session_regime in ("trending_up", "trending_down")

    if time(9, 30) <= t < time(10, 30):
        return 1.0, "First-hour high-reliability window"
    if time(14, 30) <= t < time(15, 10):
        return 1.0, "Last-hour acceleration window"
    if time(10, 30) <= t < time(13, 0):
        if trending:
            return 1.0, "Midday trending — full reliability"
        return 0.75, "Midday moderate-reliability window"
    if time(13, 0) <= t < time(14, 0):
        if trending:
            return 0.75, "Post-lunch trending — moderate reliability"
        return 0.5, "Post-lunch low-reliability window"
    if time(14, 0) <= t < time(14, 30):
        return 0.75, "Pre-Europe-open moderate window"
    return 0.5, "Outside high-reliability window"


def layer8_candle_momentum(x: LayerInputs) -> tuple[float, str]:
    """Score consecutive same-direction candle runs from the tail.

    Returns up to +/-2 based on run length and cumulative body movement.
    This layer is added *after* the RVOL clamp so momentum cannot be
    suppressed by low-volume flags during genuine trending sessions.
    """

    candles = x.candles
    if len(candles) < 3:
        return 0.0, "Insufficient candles for momentum"

    is_green = candles[-1].close >= candles[-1].open
    run_len = 1
    for i in range(len(candles) - 2, -1, -1):
        if (candles[i].close >= candles[i].open) == is_green:
            run_len += 1
        else:
            break

    if run_len < 3:
        return 0.0, f"No momentum run (last run: {run_len} candles)"

    run_start = len(candles) - run_len
    run_candles = candles[run_start:]
    move_pts = (
        run_candles[-1].close - run_candles[0].open
        if is_green
        else run_candles[0].open - run_candles[-1].close
    )

    sign = 1.0 if is_green else -1.0
    label = "bullish" if is_green else "bearish"

    if run_len >= 5 and move_pts >= 100:
        return sign * 2.0, f"Strong {label} run: {run_len} candles, {move_pts:.0f} pts"
    if run_len >= 4 and move_pts >= 70:
        return sign * 1.5, f"Solid {label} run: {run_len} candles, {move_pts:.0f} pts"
    if run_len >= 3 and move_pts >= 50:
        return sign * 1.0, f"Emerging {label} run: {run_len} candles, {move_pts:.0f} pts"
    if run_len >= 3:
        return sign * 0.5, f"Weak {label} run: {run_len} candles, {move_pts:.0f} pts"

    return 0.0, f"Run below threshold ({run_len} candles, {move_pts:.0f} pts)"


def aggregate(x: LayerInputs) -> Dict[str, object]:
    """Compute all layers and return the structured score dict."""

    l1, l1n = layer1_book_structure(x)
    l2, l2n = layer2_order_flow(x)
    l3, l3n = layer3_liquidity(x)
    l4, l4n = layer4_option_chain(x)
    l5, l5n = layer5_vwap_volume(x)
    l6, l6n = layer6_cross_market(x)
    mult, l7n = layer7_multiplier(x)
    l8, l8n = layer8_candle_momentum(x)

    raw_base = l1 + l2 + l3 + l4 + l5 + l6
    if x.rvol < 0.5:
        raw_base = max(min(raw_base, 6.0), -6.0)

    # Momentum is added after the RVOL clamp so trending moves aren't suppressed
    raw = raw_base + l8
    adjusted = raw * mult

    trending = x.session_regime in ("trending_up", "trending_down")
    strong_move = abs(x.intraday_move_pct) > 0.3
    threshold = 3.0 if (trending or strong_move) else 4.0

    direction = "long" if adjusted >= threshold else "short" if adjusted <= -threshold else "neutral"

    return {
        "layer1": (l1, l1n),
        "layer2": (l2, l2n),
        "layer3": (l3, l3n),
        "layer4": (l4, l4n),
        "layer5": (l5, l5n),
        "layer6": (l6, l6n),
        "layer7_multiplier": (mult, l7n),
        "layer8": (l8, l8n),
        "raw_score": raw,
        "adjusted_score": adjusted,
        "direction": direction,
    }
