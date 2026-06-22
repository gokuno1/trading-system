"""Daily P&L tracking and circuit breaker.

The intraday loop calls :func:`check_daily_gate` before emitting any new trade
card. If the daily-loss limit has been hit OR concurrent-position cap is full,
the gate refuses the new trade with a list of human-readable reasons.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List

from ..config import SystemConfig
from ..persistence.store import Store


@dataclass
class DailyGate:
    passed: bool
    reasons: List[str]
    daily_realized_inr: float
    daily_unrealized_inr: float
    daily_loss_pct: float
    open_positions: int


def check_daily_gate(cfg: SystemConfig, store: Store, today: date | None = None) -> DailyGate:
    today = today or datetime.now().date()
    realized = store.daily_realized_pnl(today)
    unrealized = store.open_unrealized_pnl()
    open_count = store.count_open_positions()
    capital = cfg.capital.total_inr
    total_pnl = realized + unrealized
    loss_pct = (-total_pnl / capital * 100.0) if total_pnl < 0 else 0.0

    reasons: List[str] = []
    if loss_pct >= cfg.risk.daily.max_loss_pct:
        reasons.append(
            f"Daily loss circuit breaker tripped: {loss_pct:.2f}% vs cap {cfg.risk.daily.max_loss_pct:.2f}%."
        )
    if open_count >= cfg.risk.daily.max_concurrent_positions:
        reasons.append(
            f"Open positions ({open_count}) at concurrent cap ({cfg.risk.daily.max_concurrent_positions})."
        )

    return DailyGate(
        passed=len(reasons) == 0,
        reasons=reasons,
        daily_realized_inr=realized,
        daily_unrealized_inr=unrealized,
        daily_loss_pct=loss_pct,
        open_positions=open_count,
    )
