"""Risk agent — runs all gates and produces a final TradeCard or rejection.

Order of gates (any failure short-circuits to a no-trade decision):
  1. Hard session-time rules (no entry before 09:20, after 15:10).
  2. Daily-loss circuit breaker + concurrent-position cap.
  3. Microstructure score gate (|adjusted| ≥ configured threshold).
  4. Macro alignment check (penalty / hard reject by config).
  5. Stop/target derivation (must produce a valid R:R ≥ min).
  6. Position sizing (must yield ≥ 1 lot under all caps).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..analysis.option_math import OptionFeatures
from ..config import SystemConfig
from ..persistence.store import Store
from ..risk.pnl_tracker import check_daily_gate
from ..risk.sizer import size_option_trade
from ..risk.stop_logic import derive_stop_target
from ..state import (
    AgentState,
    MicrostructureScore,
    RiskGateResult,
    TradeCard,
)


def _is_macro_aligned(direction: str, state: AgentState) -> bool:
    if state.macro is None:
        return False
    bias = (
        state.macro.nifty_directional_bias
        if state.instrument.upper() == "NIFTY"
        else state.macro.banknifty_directional_bias
    )
    bullish = {"bullish", "lean_bullish"}
    bearish = {"bearish", "lean_bearish"}
    if direction == "long":
        return bias in bullish
    if direction == "short":
        return bias in bearish
    return False


def _pick_strike(of: OptionFeatures, direction: str, step: int) -> tuple[int, str, float, float]:
    """Pick ATM±1step strike. Returns (strike, option_type, entry_premium, delta_assumption)."""

    if direction == "long":
        # buy ATM CE for cleaner delta; entry = mid of bid/ask
        strike_target = of.atm_strike
        for r in of.strikes:
            if r.strike == strike_target:
                entry = (r.ce_bid + r.ce_ask) / 2 if (r.ce_bid and r.ce_ask) else r.ce_ltp
                return strike_target, "CE", entry, 0.5
    else:
        strike_target = of.atm_strike
        for r in of.strikes:
            if r.strike == strike_target:
                entry = (r.pe_bid + r.pe_ask) / 2 if (r.pe_bid and r.pe_ask) else r.pe_ltp
                return strike_target, "PE", entry, -0.5
    raise ValueError("ATM strike not found in chain")


def evaluate(
    state: AgentState,
    cfg: SystemConfig,
    store: Store,
    instrument_label: str,
    lot_size: int,
) -> AgentState:
    """Apply all risk gates. Mutate ``state`` to attach a ``trade_card`` or risk_gate."""

    score: Optional[MicrostructureScore] = state.microstructure
    if score is None:
        state.fail("No microstructure score computed.")
        state.risk_gate = RiskGateResult(passed=False, reasons_rejected=["no_microstructure"])
        return state

    # Gate 1 — session time
    t = state.now_ist.time()
    if t < cfg.risk.session_rules.no_entry_before:
        state.risk_gate = RiskGateResult(passed=False, reasons_rejected=["before_no_entry_window"])
        return state
    if t > cfg.risk.session_rules.no_entry_after:
        state.risk_gate = RiskGateResult(passed=False, reasons_rejected=["after_no_entry_window"])
        return state

    # Gate 2 — daily P&L + concurrent
    daily = check_daily_gate(cfg, store)
    if not daily.passed:
        state.risk_gate = RiskGateResult(
            passed=False,
            reasons_rejected=daily.reasons,
            daily_loss_pct=daily.daily_loss_pct,
            open_positions=daily.open_positions,
        )
        return state

    # Gate 3 — microstructure score
    if abs(score.adjusted_score) < cfg.risk.score_gates.microstructure_min_abs_score:
        state.risk_gate = RiskGateResult(
            passed=False,
            reasons_rejected=[f"microstructure_score_below_threshold ({score.adjusted_score:.2f})"],
            open_positions=daily.open_positions,
        )
        return state

    direction = score.direction
    if direction == "neutral":
        state.risk_gate = RiskGateResult(passed=False, reasons_rejected=["neutral_direction"])
        return state

    # Gate 4 — macro alignment
    macro_aligned = _is_macro_aligned(direction, state)
    if cfg.risk.score_gates.macro_alignment_required and not macro_aligned:
        state.risk_gate = RiskGateResult(
            passed=False,
            reasons_rejected=["macro_not_aligned"],
            open_positions=daily.open_positions,
        )
        return state
    if not macro_aligned:
        # Only penalize, do not reject (per config)
        score.adjusted_score = score.adjusted_score - (
            cfg.risk.score_gates.macro_contradiction_penalty
            * (1.0 if direction == "long" else -1.0)
        )
        if abs(score.adjusted_score) < cfg.risk.score_gates.microstructure_min_abs_score:
            state.risk_gate = RiskGateResult(
                passed=False,
                reasons_rejected=["score_below_threshold_after_macro_penalty"],
                open_positions=daily.open_positions,
            )
            return state

    # Gate 5 — stop/target
    of: OptionFeatures = score.raw_data["option_features_full"] if "option_features_full" in score.raw_data else None  # type: ignore
    # We re-fetch the OptionFeatures via score.raw_data — but compute_features object isn't JSON serializable.
    # Instead use the lightweight dict and reconstruct what stop_logic needs.
    raw = score.raw_data
    spot = float(raw.get("spot", 0))
    atr = float(raw.get("atr", 0))
    session_high = float(raw.get("session_high", spot))
    session_low = float(raw.get("session_low", spot))
    walls = raw.get("walls", {}) or {}
    bid_wall_price = walls.get("bid", (None, None))[0]
    ask_wall_price = walls.get("ask", (None, None))[0]
    of_dict = raw.get("option_features", {})
    _spot = spot
    _of = of_dict

    class _OFShim:
        spot = _spot
        highest_call_oi_strike = int(_of.get("highest_call_oi_strike", 0))
        highest_put_oi_strike = int(_of.get("highest_put_oi_strike", 0))
        expected_range_high = int(_of.get("highest_call_oi_strike", 0))
        expected_range_low = int(_of.get("highest_put_oi_strike", 0))
        atm_strike = int(_of.get("atm_strike", 0))
        atm_straddle_price = float(_of.get("atm_straddle_price", 0))

    # Strike selection + entry premium — derive from chain via score raw_data
    strike = _OFShim.atm_strike
    if direction == "long":
        option_type = "CE"
        entry = float(raw.get("atm_call_premium", _OFShim.atm_straddle_price / 2))
        delta_assumption = 0.5
    else:
        option_type = "PE"
        entry = float(raw.get("atm_put_premium", _OFShim.atm_straddle_price / 2))
        delta_assumption = 0.5  # absolute delta for PE

    spec = derive_stop_target(
        direction=direction,
        underlying_ltp=spot,
        option_entry=entry,
        of=_OFShim,  # type: ignore[arg-type]
        atr=atr,
        ask_wall_price=ask_wall_price,
        bid_wall_price=bid_wall_price,
        session_high=session_high,
        session_low=session_low,
        vwap_price=float(raw.get("vwap_proxy_close", spot)),
        delta_assumption=delta_assumption,
        max_atr_multiple=cfg.risk.stop_loss.max_atr_multiple,
        hard_max_atr_multiple=cfg.risk.stop_loss.hard_max_atr_multiple,
        min_reward_risk=cfg.risk.per_trade.min_reward_risk,
    )
    if spec is None:
        state.risk_gate = RiskGateResult(
            passed=False,
            reasons_rejected=["no_valid_stop_target_with_min_reward_risk"],
        )
        return state

    # Gate 6 — sizing
    expiry_today = False  # caller can set this from current expiry calendar
    sized = size_option_trade(
        capital_inr=cfg.capital.total_inr,
        risk_cfg=cfg.risk,
        entry_price=spec.option_entry,
        stop_loss_price=spec.option_stop,
        lot_size=lot_size,
        expiry_today=expiry_today,
    )
    if sized.lots == 0:
        state.risk_gate = RiskGateResult(
            passed=False,
            reasons_rejected=sized.reasons,
            warnings=sized.warnings,
        )
        return state

    state.risk_gate = RiskGateResult(passed=True, warnings=sized.warnings)
    state.trade_card = TradeCard(
        instrument=instrument_label,
        direction=direction,
        underlying_ltp=spot,
        selected_strike=strike,
        option_type=option_type,
        expiry=of_dict.get("expiry", ""),
        entry_price=spec.option_entry,
        stop_loss=spec.option_stop,
        target_1=spec.option_target_1,
        target_2=spec.option_target_2,
        reward_risk=spec.reward_risk,
        position_size_lots=sized.lots,
        risk_amount_inr=sized.risk_amount_inr,
        notional_inr=sized.notional_inr,
        adjusted_score=score.adjusted_score,
        macro_aligned=macro_aligned,
        invalidation_conditions=[
            f"Underlying breaks {spec.underlying_stop:.1f}",
            f"Adjusted score flips below {cfg.risk.score_gates.microstructure_min_abs_score:.1f}",
        ],
        layer_breakdown=score.layer_notes,
        notes=spec.notes,
    )
    return state
