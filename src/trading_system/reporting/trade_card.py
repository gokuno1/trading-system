"""Render a TradeCard exactly per the microstructure skill spec."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..state import MicrostructureScore, TradeCard


TEMPLATE = """\
═══════════════════════════════════════════════════
  TRADE CARD — {instrument}
  Generated: {generated}
═══════════════════════════════════════════════════

  Direction:    {direction}
  Conviction:   {conviction} (Adjusted Score: {adjusted_score:.2f})
  Instrument:   {leg}

  Entry:        ₹{entry:.2f} (Underlying LTP: ₹{spot:.2f})
  Stop-Loss:    ₹{stop:.2f}
  Target 1:     ₹{t1:.2f} (R:R = {rr:.2f}:1)
{t2_line}
  Position Size: {lots} lot(s)
  Risk Amount:   ₹{risk_amount:,.0f}
  Notional:      ₹{notional:,.0f}

  Macro Aligned: {macro_aligned}

───────────────────────────────────────────────────
  LAYER BREAKDOWN
───────────────────────────────────────────────────
{layer_lines}

───────────────────────────────────────────────────
  INVALIDATION
───────────────────────────────────────────────────
{invalidation_lines}

  Notes: {notes}
═══════════════════════════════════════════════════
"""


def _conviction_label(score: float) -> str:
    a = abs(score)
    if a >= 8:
        return "Strong"
    if a >= 5:
        return "Moderate"
    return "Weak"


def format_trade_card(card: TradeCard, score: Optional[MicrostructureScore] = None) -> str:
    leg = f"{card.instrument} {card.selected_strike} {card.option_type} (expiry {card.expiry})"
    t2_line = (
        f"  Target 2:     ₹{card.target_2:.2f}\n"
        if card.target_2 is not None
        else ""
    )
    layer_lines = "\n".join(f"  {k}: {v}" for k, v in (card.layer_breakdown or {}).items())
    invalidation_lines = "\n".join(f"  - {c}" for c in card.invalidation_conditions)
    return TEMPLATE.format(
        instrument=card.instrument,
        generated=datetime.utcnow().isoformat() + "Z",
        direction=card.direction.upper(),
        conviction=_conviction_label(card.adjusted_score),
        adjusted_score=card.adjusted_score,
        leg=leg,
        entry=card.entry_price,
        spot=card.underlying_ltp,
        stop=card.stop_loss,
        t1=card.target_1,
        rr=card.reward_risk,
        t2_line=t2_line,
        lots=card.position_size_lots,
        risk_amount=card.risk_amount_inr,
        notional=card.notional_inr,
        macro_aligned="YES" if card.macro_aligned else "NO (penalty applied)",
        layer_lines=layer_lines or "  (no layer breakdown)",
        invalidation_lines=invalidation_lines or "  (none)",
        notes=card.notes or "—",
    )


NO_TRADE_TEMPLATE = """\
═══════════════════════════════════════════════════
  NO ACTIONABLE SETUP — {instrument}
  {generated}
═══════════════════════════════════════════════════

  Reasons:
{reasons}

  Microstructure score (adjusted): {adjusted}
  Direction:                       {direction}

  Layer breakdown:
{layer_lines}
═══════════════════════════════════════════════════
"""


def format_no_trade(state) -> str:
    score = state.microstructure
    rg = state.risk_gate
    reasons = "\n".join(f"  - {r}" for r in (rg.reasons_rejected if rg else [])) or "  - unknown"
    layer_lines = "\n".join(
        f"  {k}: {v}" for k, v in (score.layer_notes if score else {}).items()
    ) or "  (no score computed)"
    return NO_TRADE_TEMPLATE.format(
        instrument=state.instrument,
        generated=datetime.utcnow().isoformat() + "Z",
        reasons=reasons,
        adjusted=f"{score.adjusted_score:.2f}" if score else "n/a",
        direction=score.direction if score else "n/a",
        layer_lines=layer_lines,
    )
