"""Pure-Python microstructure math: VWAP, ATR, RVOL, depth imbalance.

All functions are deterministic and accept simple lists/dicts, so they can
be unit-tested without any HTTP calls.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


@dataclass
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def parse_candles(payload: Dict) -> List[Candle]:
    """Upstox intraday candle response → list of Candle.

    The API returns candles oldest-first by default but we re-sort for safety.
    """

    raw = payload.get("data", {}).get("candles", []) or []
    candles = [
        Candle(
            timestamp=c[0],
            open=float(c[1]),
            high=float(c[2]),
            low=float(c[3]),
            close=float(c[4]),
            volume=float(c[5]),
        )
        for c in raw
    ]
    candles.sort(key=lambda c: c.timestamp)
    return candles


def vwap(candles: Sequence[Candle]) -> float:
    if not candles:
        return 0.0
    num = sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in candles)
    den = sum(c.volume for c in candles) or 1.0
    return num / den


def vwap_slope(candles: Sequence[Candle], split: int = 6) -> float:
    """Approximate VWAP slope: difference between recent vs early VWAP."""

    if len(candles) < split * 2:
        return 0.0
    early = vwap(candles[:split])
    recent = vwap(candles[-split:])
    if early == 0:
        return 0.0
    return (recent - early) / early


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs: List[float] = []
    prev_close = candles[0].close
    for c in candles[1:]:
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)
        prev_close = c.close
    period = min(period, len(trs))
    return sum(trs[-period:]) / period


def session_high_low(candles: Sequence[Candle]) -> Tuple[float, float]:
    if not candles:
        return 0.0, 0.0
    return max(c.high for c in candles), min(c.low for c in candles)


def cumulative_delta_proxy(candles: Sequence[Candle]) -> List[float]:
    """When trade-by-trade aggressor data isn't available, infer delta from
    candle direction × volume. Bullish candle → +volume, bearish → -volume.
    """

    delta = 0.0
    series: List[float] = []
    for c in candles:
        if c.close > c.open:
            delta += c.volume
        elif c.close < c.open:
            delta -= c.volume
        series.append(delta)
    return series


def rvol(today_volume: float, expected_volume: float) -> float:
    if expected_volume <= 0:
        return 0.0
    return today_volume / expected_volume


def depth_imbalance_l1(best_bid_qty: float, best_ask_qty: float) -> float:
    den = best_bid_qty + best_ask_qty
    if den <= 0:
        return 0.0
    return (best_bid_qty - best_ask_qty) / den


def weighted_book_imbalance(bids: Sequence[Tuple[float, float]], asks: Sequence[Tuple[float, float]]) -> float:
    """``bids``/``asks`` are 5 (price, qty) pairs ordered best-first."""

    weights = [5, 4, 3, 2, 1]
    wb = sum(q * w for (_, q), w in zip(bids, weights))
    wa = sum(q * w for (_, q), w in zip(asks, weights))
    den = wb + wa
    if den <= 0:
        return 0.0
    return (wb - wa) / den


def detect_walls(
    bids: Sequence[Tuple[float, float]], asks: Sequence[Tuple[float, float]], multiple: float = 3.0
) -> Dict[str, Tuple[float, float]]:
    """Return any single level whose qty > ``multiple`` × average level qty."""

    out: Dict[str, Tuple[float, float]] = {}
    if bids:
        avg_b = statistics.mean(q for _, q in bids) or 0.0
        for p, q in bids:
            if avg_b > 0 and q > multiple * avg_b:
                out["bid"] = (p, q)
                break
    if asks:
        avg_a = statistics.mean(q for _, q in asks) or 0.0
        for p, q in asks:
            if avg_a > 0 and q > multiple * avg_a:
                out["ask"] = (p, q)
                break
    return out
