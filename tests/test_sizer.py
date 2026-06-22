from trading_system.config import (
    DailyRisk,
    PerTradeRisk,
    RiskConfig,
    ScoreGates,
    SessionRules,
    StopLossConfig,
)
from trading_system.risk.sizer import size_option_trade


def _risk() -> RiskConfig:
    return RiskConfig(
        profile="balanced",
        per_trade=PerTradeRisk(max_risk_pct=1.0, max_notional_pct=10.0,
                               max_premium_pct=1.5, min_reward_risk=1.5),
        daily=DailyRisk(max_loss_pct=3.0, max_concurrent_positions=3),
        session_rules=SessionRules(
            no_entry_before_ist="09:20", no_entry_after_ist="15:10",
            flatten_all_by_ist="15:20", expiry_day_size_multiplier=0.5,
            rvol_floor_for_trade=0.5,
        ),
        score_gates=ScoreGates(
            microstructure_min_abs_score=5.0, macro_alignment_required=False,
            macro_contradiction_penalty=1.0,
        ),
        stop_loss=StopLossConfig(max_atr_multiple=1.5, hard_max_atr_multiple=2.0),
    )


def test_small_capital_one_lot_too_risky():
    """At ₹3L capital, a NIFTY ATM option with 50pt option-stop blows the budget."""
    out = size_option_trade(
        capital_inr=300_000,
        risk_cfg=_risk(),
        entry_price=150,
        stop_loss_price=100,  # 50 pt stop × 75 lot = ₹3,750 > 1% of ₹3L = ₹3,000
        lot_size=75,
    )
    assert out.lots == 0
    assert any("budget" in r.lower() for r in out.reasons)


def test_large_capital_passes_size():
    out = size_option_trade(
        capital_inr=1_000_000,
        risk_cfg=_risk(),
        entry_price=150,
        stop_loss_price=100,
        lot_size=75,
    )
    # 1% of ₹10L = ₹10,000 ; risk per lot ₹3,750 → 2 lots
    assert out.lots == 2
    assert out.risk_amount_inr == 2 * 50 * 75


def test_invalid_pricing():
    out = size_option_trade(
        capital_inr=300_000,
        risk_cfg=_risk(),
        entry_price=100,
        stop_loss_price=120,  # stop above entry for a long
        lot_size=75,
    )
    assert out.lots == 0
