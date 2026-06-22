"""Paper-trade engine.

Records simulated entries/exits at the *current bid/ask midpoint* for the
chosen option leg. Does NOT model slippage explicitly — operators should
mentally subtract 0.5–1.0 points per leg from the reported P&L when comparing
to live performance.

The engine has three responsibilities:
  1. ``open_position`` — record a new paper trade given a TradeCard.
  2. ``mark_to_market`` — given fresh option quotes, refresh unrealized P&L
     for all open positions and trip stops/targets.
  3. ``close_position`` — record an exit, compute realized P&L, persist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..persistence.schemas import TradeRecord
from ..persistence.store import Store
from ..state import TradeCard


@dataclass
class MarkToMarketResult:
    trade_id: int
    instrument: str
    open_price: float
    current_price: float
    unrealized_pnl_inr: float
    should_exit: bool
    exit_reason: Optional[str]


class PaperEngine:
    def __init__(self, store: Store):
        self.store = store

    def open_position(self, card: TradeCard, lot_size: int, now: Optional[datetime] = None) -> int:
        now = now or datetime.utcnow()
        rec = TradeRecord(
            instrument=card.instrument,
            direction="long",  # signal-only / long-options Phase 1
            option_type=card.option_type,
            strike=card.selected_strike,
            expiry=card.expiry,
            lot_size=lot_size,
            lots=card.position_size_lots,
            entry_time=now,
            entry_price=card.entry_price,
            stop_loss=card.stop_loss,
            target_1=card.target_1,
            target_2=card.target_2,
            underlying_at_entry=card.underlying_ltp,
            adjusted_score=card.adjusted_score,
            macro_aligned=card.macro_aligned,
        )
        return self.store.insert_trade(rec)

    def mark_to_market(self, current_prices: Dict[str, float]) -> List[MarkToMarketResult]:
        """current_prices: mapping ``"<instrument>:<strike>:<CE|PE>"`` → premium LTP."""

        results: List[MarkToMarketResult] = []
        for t in self.store.open_trades():
            key = f"{t.instrument}:{t.strike}:{t.option_type}"
            cp = current_prices.get(key)
            if cp is None:
                continue
            unrealized = (cp - t.entry_price) * t.lots * t.lot_size
            should_exit = False
            reason: Optional[str] = None
            if cp <= t.stop_loss:
                should_exit, reason = True, "stop_loss_hit"
            elif cp >= t.target_1 and t.target_2 is None:
                should_exit, reason = True, "target_1_hit"
            elif t.target_2 is not None and cp >= t.target_2:
                should_exit, reason = True, "target_2_hit"
            results.append(
                MarkToMarketResult(
                    trade_id=t.id or -1,
                    instrument=t.instrument,
                    open_price=t.entry_price,
                    current_price=cp,
                    unrealized_pnl_inr=unrealized,
                    should_exit=should_exit,
                    exit_reason=reason,
                )
            )
        return results

    def close_position(self, trade_id: int, exit_price: float, exit_reason: str, now: Optional[datetime] = None) -> float:
        now = now or datetime.utcnow()
        # Re-load to compute realized P&L
        with self.store._conn() as c:  # noqa: SLF001 — single intentional touchpoint
            row = c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        t = self.store._row_to_trade(row)  # noqa: SLF001
        realized = (exit_price - t.entry_price) * t.lots * t.lot_size
        self.store.close_trade(trade_id, exit_price, exit_reason, now, realized)
        return realized

    def force_flatten_all(self, current_prices: Dict[str, float], now: Optional[datetime] = None) -> List[Tuple[int, float]]:
        """Close everything — used at 15:20 IST and on the daily-loss circuit breaker."""

        out: List[Tuple[int, float]] = []
        for t in self.store.open_trades():
            key = f"{t.instrument}:{t.strike}:{t.option_type}"
            cp = current_prices.get(key, t.entry_price)
            realized = self.close_position(t.id or -1, cp, "forced_flatten", now)
            out.append((t.id or -1, realized))
        return out
