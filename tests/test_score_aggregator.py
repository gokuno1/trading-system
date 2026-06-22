from datetime import datetime, time

import pytest

from trading_system.analysis.microstructure import Candle
from trading_system.analysis.option_math import OptionFeatures, StrikeRow
from trading_system.analysis.score_aggregator import LayerInputs, aggregate


def _of() -> OptionFeatures:
    rows = [
        StrikeRow(s, 100, 100, 1e6, 1e6, 1e5, 1e5, 13.0, 13.0, 99, 101, 99, 101)
        for s in [22300, 22400, 22500, 22600, 22700]
    ]
    return OptionFeatures(
        spot=22500,
        expiry="2026-05-08",
        strikes=rows,
        pcr_oi=1.3,
        pcr_volume=1.1,
        max_pain=22500,
        highest_call_oi_strike=22700,
        highest_put_oi_strike=22300,
        atm_strike=22500,
        atm_straddle_price=200.0,
        atm_iv=13.0,
        iv_skew_25d=1.2,
        expected_range_low=22300,
        expected_range_high=22700,
    )


def _candles_up() -> list:
    base = 22400
    out = []
    for i in range(20):
        c = Candle(timestamp=f"2026-04-30T{9 + i // 12:02d}:{(i*5) % 60:02d}:00",
                   open=base + i, high=base + i + 5, low=base + i - 2,
                   close=base + i + 4, volume=10000)
        out.append(c)
    return out


def test_bullish_setup_scores_long():
    of = _of()
    inp = LayerInputs(
        l1_imbalance=0.5, weighted_imbalance=0.5, has_bid_wall_near=False,
        has_ask_wall_near=False, spread_bps=2.0, delta_trend=1.0,
        price_at_session_high=False, price_at_session_low=False,
        depth_ratio=1.6, rvol=1.4, option_features=of, candles=_candles_up(),
        last_price=22480, related_alignment=0.9, now_ist=time(9, 45),
        is_event_day=False, session_regime="trending_up",
    )
    out = aggregate(inp)
    assert out["raw_score"] >= 5
    assert out["adjusted_score"] >= 5
    assert out["direction"] == "long"


def test_drift_kills_score():
    of = _of()
    inp = LayerInputs(
        l1_imbalance=0.5, weighted_imbalance=0.5, has_bid_wall_near=False,
        has_ask_wall_near=False, spread_bps=2.0, delta_trend=1.0,
        price_at_session_high=False, price_at_session_low=False,
        depth_ratio=1.6, rvol=1.4, option_features=of, candles=_candles_up(),
        last_price=22480, related_alignment=0.9, now_ist=time(9, 45),
        is_event_day=False, session_regime="drift",
    )
    out = aggregate(inp)
    assert out["adjusted_score"] == 0
    assert out["direction"] == "neutral"


def test_low_rvol_caps_score():
    of = _of()
    inp = LayerInputs(
        l1_imbalance=0.9, weighted_imbalance=0.9, has_bid_wall_near=False,
        has_ask_wall_near=False, spread_bps=2.0, delta_trend=1.0,
        price_at_session_high=False, price_at_session_low=False,
        depth_ratio=2.5, rvol=0.4, option_features=of, candles=_candles_up(),
        last_price=22480, related_alignment=0.9, now_ist=time(9, 45),
        is_event_day=False, session_regime="trending_up",
    )
    out = aggregate(inp)
    # rvol < 0.7 caps every individual layer at ±1; six layers max → ±6, mult 1
    assert out["raw_score"] <= 6
