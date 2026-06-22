"""Pydantic models for persisted records.

These mirror the SQLite tables. Keeping them as Pydantic lets the same
shapes round-trip to MongoDB once that MCP is available — no changes to
business code needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class TradeRecord(BaseModel):
    id: Optional[int] = None
    instrument: str
    direction: Literal["long", "short"]
    option_type: Literal["CE", "PE"]
    strike: int
    expiry: str
    lot_size: int
    lots: int
    entry_time: datetime
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: Optional[float] = None
    underlying_at_entry: float
    adjusted_score: float
    macro_aligned: bool

    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    realized_pnl_inr: float = 0.0

    status: Literal["OPEN", "CLOSED"] = "OPEN"


class SignalSnapshot(BaseModel):
    id: Optional[int] = None
    instrument: str
    timestamp: datetime
    raw_score: float
    adjusted_score: float
    direction: str
    layer_breakdown_json: str
    macro_regime: Optional[str] = None
    macro_bias: Optional[str] = None


class DailyMetric(BaseModel):
    day: str  # YYYY-MM-DD
    realized_pnl_inr: float
    unrealized_pnl_inr: float
    n_trades: int
    n_wins: int
    n_losses: int
    capital_inr: float
    pnl_pct: float
