"""End-of-day report — summarises trades, P&L, and regime alignment."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from ..persistence.schemas import DailyMetric
from ..persistence.store import Store


def build_daily_report(store: Store, day: date, capital: float) -> DailyMetric:
    realized = store.daily_realized_pnl(day)
    n_trades = 0
    n_wins = 0
    n_losses = 0
    with store._conn() as c:  # noqa: SLF001
        rows = c.execute(
            "SELECT realized_pnl_inr FROM trades WHERE substr(entry_time,1,10)=? AND status='CLOSED'",
            (day.isoformat(),),
        ).fetchall()
    for r in rows:
        n_trades += 1
        pnl = float(r[0])
        if pnl > 0:
            n_wins += 1
        elif pnl < 0:
            n_losses += 1
    pnl_pct = realized / capital * 100.0
    metric = DailyMetric(
        day=day.isoformat(),
        realized_pnl_inr=realized,
        unrealized_pnl_inr=0.0,
        n_trades=n_trades,
        n_wins=n_wins,
        n_losses=n_losses,
        capital_inr=capital,
        pnl_pct=pnl_pct,
    )
    store.upsert_daily_metric(metric)
    return metric


def render_daily_text(metric: DailyMetric) -> str:
    win_rate = metric.n_wins / metric.n_trades * 100.0 if metric.n_trades else 0.0
    return (
        f"Daily Report — {metric.day}\n"
        f"  Realized P&L:  ₹{metric.realized_pnl_inr:,.0f} ({metric.pnl_pct:+.2f}% of capital)\n"
        f"  Trades:        {metric.n_trades} (W:{metric.n_wins} / L:{metric.n_losses}, win-rate {win_rate:.1f}%)\n"
        f"  Capital base:  ₹{metric.capital_inr:,.0f}\n"
    )
