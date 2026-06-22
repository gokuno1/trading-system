"""Position sizing — converts a risk budget into a whole number of lots.

Three hard caps apply (any single one short-circuits the size):
  1. Per-trade rupee risk ≤ ``max_risk_pct`` × capital.
  2. Notional value (entry × lot × lots) ≤ ``max_notional_pct`` × capital.
  3. Premium-at-risk for options ≤ ``max_premium_pct`` × capital
     (option premiums can go to zero — separate cap from stop-based risk).

If any cap forces lots to 0, the function returns 0 and a list of human-readable
reasons. The caller MUST refuse to emit a trade card when lots == 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

from ..config import RiskConfig


@dataclass
class SizingResult:
    lots: int
    risk_amount_inr: float
    notional_inr: float
    premium_at_risk_inr: float
    reasons: List[str]
    warnings: List[str]


def size_option_trade(
    *,
    capital_inr: float,
    risk_cfg: RiskConfig,
    entry_price: float,
    stop_loss_price: float,
    lot_size: int,
    expiry_today: bool = False,
) -> SizingResult:
    """Size an option leg.

    ``entry_price``/``stop_loss_price`` are option premium prices, NOT spot.
    ``lot_size`` is the contract multiplier (e.g. 75 for NIFTY).
    ``expiry_today`` halves the size per the skill's expiry-day rule.
    """

    reasons: List[str] = []
    warnings: List[str] = []

    if entry_price <= 0 or stop_loss_price <= 0 or stop_loss_price >= entry_price:
        return SizingResult(
            lots=0,
            risk_amount_inr=0.0,
            notional_inr=0.0,
            premium_at_risk_inr=0.0,
            reasons=["invalid entry/stop pricing for a long option leg"],
            warnings=warnings,
        )

    stop_distance = entry_price - stop_loss_price
    rr_min = risk_cfg.per_trade.min_reward_risk

    risk_budget = capital_inr * (risk_cfg.per_trade.max_risk_pct / 100.0)
    risk_per_lot = stop_distance * lot_size

    if risk_per_lot > risk_budget:
        reasons.append(
            f"One lot risks ₹{risk_per_lot:,.0f} which exceeds the per-trade budget ₹{risk_budget:,.0f} "
            "(small-capital constraint — consider tighter strike or wait for better setup)."
        )
        return SizingResult(0, 0.0, 0.0, 0.0, reasons, warnings)

    lots = int(math.floor(risk_budget / risk_per_lot))
    if expiry_today:
        lots = max(1, lots // 2)
        warnings.append("Expiry day — size halved per session rule.")

    notional_cap = capital_inr * (risk_cfg.per_trade.max_notional_pct / 100.0)
    while lots > 0 and lots * entry_price * lot_size > notional_cap:
        lots -= 1

    premium_cap = capital_inr * (risk_cfg.per_trade.max_premium_pct / 100.0)
    while lots > 0 and lots * entry_price * lot_size > premium_cap:
        lots -= 1

    if lots == 0:
        reasons.append("Notional or premium cap reduced size to zero.")
        return SizingResult(0, 0.0, 0.0, 0.0, reasons, warnings)

    risk_amount = lots * risk_per_lot
    notional = lots * entry_price * lot_size
    premium_at_risk = notional  # for a long option, full premium is at risk

    return SizingResult(
        lots=lots,
        risk_amount_inr=risk_amount,
        notional_inr=notional,
        premium_at_risk_inr=premium_at_risk,
        reasons=reasons,
        warnings=warnings,
    )
