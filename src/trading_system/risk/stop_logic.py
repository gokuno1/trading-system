"""Stop-loss and target derivation rules from the microstructure skill.

Rules in priority order (longs):
  1. Below the strongest bid wall in the L2 book (if it exists)
  2. Below the session low or nearest HVN below entry
  3. Below VWAP if entry is above VWAP
  4. Hard cap: 1.5 × intraday ATR

For shorts, mirror.

Targets:
  1. Nearest opposing wall (ask wall for longs, bid wall for shorts)
  2. Highest call OI (longs) / highest put OI (shorts)
  3. VWAP (conservative, on pullback entries)

The functions here work on the OPTION leg pricing because we are trading
options. A common simplification: stop and target are expressed as % moves
on the option's premium derived from the underlying move and an assumed
delta of 0.5 for ATM, scaled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..analysis.option_math import OptionFeatures


@dataclass
class StopTargetSpec:
    underlying_stop: float
    underlying_target_1: float
    underlying_target_2: Optional[float]
    delta_assumption: float
    option_entry: float
    option_stop: float
    option_target_1: float
    option_target_2: Optional[float]
    reward_risk: float
    notes: str


def underlying_to_option_move(under_move: float, delta: float) -> float:
    """Approximate option premium move from underlying move using assumed delta."""

    return under_move * delta


def derive_stop_target(
    *,
    direction: str,
    underlying_ltp: float,
    option_entry: float,
    of: OptionFeatures,
    atr: float,
    ask_wall_price: Optional[float] = None,
    bid_wall_price: Optional[float] = None,
    session_high: Optional[float] = None,
    session_low: Optional[float] = None,
    vwap_price: Optional[float] = None,
    delta_assumption: float = 0.5,
    max_atr_multiple: float = 1.5,
    hard_max_atr_multiple: float = 2.0,
    min_reward_risk: float = 1.5,
) -> Optional[StopTargetSpec]:
    """Build a stop/target spec on the underlying, then translate to the option."""

    if direction not in ("long", "short"):
        return None

    if direction == "long":
        candidates_stop = [bid_wall_price, session_low, vwap_price]
        stop_candidates = [c for c in candidates_stop if c is not None and c < underlying_ltp]
        if not stop_candidates:
            return None
        underlying_stop = max(stop_candidates)  # tightest stop wins
        # Cap stop distance at hard ATR multiple
        max_distance = atr * hard_max_atr_multiple
        if underlying_ltp - underlying_stop > max_distance:
            return None
        target_candidates = [ask_wall_price, of.highest_call_oi_strike, of.expected_range_high]
        target_candidates = [t for t in target_candidates if t is not None and t > underlying_ltp]
        if not target_candidates:
            return None
        underlying_target_1 = min(target_candidates)
        underlying_target_2 = of.highest_call_oi_strike if of.highest_call_oi_strike > underlying_target_1 else None
    else:
        candidates_stop = [ask_wall_price, session_high, vwap_price]
        stop_candidates = [c for c in candidates_stop if c is not None and c > underlying_ltp]
        if not stop_candidates:
            return None
        underlying_stop = min(stop_candidates)
        max_distance = atr * hard_max_atr_multiple
        if underlying_stop - underlying_ltp > max_distance:
            return None
        target_candidates = [bid_wall_price, of.highest_put_oi_strike, of.expected_range_low]
        target_candidates = [t for t in target_candidates if t is not None and t < underlying_ltp]
        if not target_candidates:
            return None
        underlying_target_1 = max(target_candidates)
        underlying_target_2 = of.highest_put_oi_strike if of.highest_put_oi_strike < underlying_target_1 else None

    underlying_stop_distance = abs(underlying_ltp - underlying_stop)
    underlying_target_1_distance = abs(underlying_target_1 - underlying_ltp)

    option_stop_move = underlying_to_option_move(underlying_stop_distance, delta_assumption)
    option_t1_move = underlying_to_option_move(underlying_target_1_distance, delta_assumption)
    option_stop = max(0.5, option_entry - option_stop_move)  # premium can't go below ~0.5
    option_target_1 = option_entry + option_t1_move

    option_t2 = None
    if underlying_target_2 is not None:
        ut2_dist = abs(underlying_target_2 - underlying_ltp)
        option_t2 = option_entry + underlying_to_option_move(ut2_dist, delta_assumption)

    rr = (option_target_1 - option_entry) / max(option_entry - option_stop, 1e-6)
    if rr < min_reward_risk:
        return None

    return StopTargetSpec(
        underlying_stop=underlying_stop,
        underlying_target_1=underlying_target_1,
        underlying_target_2=underlying_target_2,
        delta_assumption=delta_assumption,
        option_entry=option_entry,
        option_stop=option_stop,
        option_target_1=option_target_1,
        option_target_2=option_t2,
        reward_risk=rr,
        notes=(
            f"Underlying stop {underlying_stop:.1f}, target {underlying_target_1:.1f}, "
            f"delta {delta_assumption:.2f}, R:R {rr:.2f}"
        ),
    )
