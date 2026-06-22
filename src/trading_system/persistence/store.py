"""SQLite-backed persistence for trades, signals, and daily metrics.

We use a thin sync sqlite3 layer (no ORM) to keep the dependency surface
minimal. The class API is small and deliberate so a future MongoDB-backed
implementation can be a drop-in replacement (same method names).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .schemas import DailyMetric, SignalSnapshot, TradeRecord


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL,
    option_type TEXT NOT NULL,
    strike INTEGER NOT NULL,
    expiry TEXT NOT NULL,
    lot_size INTEGER NOT NULL,
    lots INTEGER NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    target_1 REAL NOT NULL,
    target_2 REAL,
    underlying_at_entry REAL NOT NULL,
    adjusted_score REAL NOT NULL,
    macro_aligned INTEGER NOT NULL,
    exit_time TEXT,
    exit_price REAL,
    exit_reason TEXT,
    realized_pnl_inr REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN'
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_entry_day ON trades(substr(entry_time, 1, 10));

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    raw_score REAL NOT NULL,
    adjusted_score REAL NOT NULL,
    direction TEXT NOT NULL,
    layer_breakdown_json TEXT NOT NULL,
    macro_regime TEXT,
    macro_bias TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);

CREATE TABLE IF NOT EXISTS daily_metrics (
    day TEXT PRIMARY KEY,
    realized_pnl_inr REAL NOT NULL,
    unrealized_pnl_inr REAL NOT NULL,
    n_trades INTEGER NOT NULL,
    n_wins INTEGER NOT NULL,
    n_losses INTEGER NOT NULL,
    capital_inr REAL NOT NULL,
    pnl_pct REAL NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- trades ----

    def insert_trade(self, t: TradeRecord) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO trades(instrument, direction, option_type, strike, expiry,
                    lot_size, lots, entry_time, entry_price, stop_loss, target_1,
                    target_2, underlying_at_entry, adjusted_score, macro_aligned, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    t.instrument, t.direction, t.option_type, t.strike, t.expiry,
                    t.lot_size, t.lots, t.entry_time.isoformat(), t.entry_price,
                    t.stop_loss, t.target_1, t.target_2, t.underlying_at_entry,
                    t.adjusted_score, 1 if t.macro_aligned else 0, t.status,
                ),
            )
            return cur.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, exit_reason: str, exit_time: datetime, realized_pnl: float) -> None:
        with self._conn() as c:
            c.execute(
                """
                UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?,
                    exit_time=?, realized_pnl_inr=? WHERE id=?
                """,
                (exit_price, exit_reason, exit_time.isoformat(), realized_pnl, trade_id),
            )

    def open_trades(self) -> List[TradeRecord]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        return [self._row_to_trade(r) for r in rows]

    def count_open_positions(self) -> int:
        with self._conn() as c:
            (n,) = c.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()
        return int(n)

    def daily_realized_pnl(self, day: date) -> float:
        with self._conn() as c:
            (val,) = c.execute(
                "SELECT COALESCE(SUM(realized_pnl_inr),0) FROM trades "
                "WHERE status='CLOSED' AND substr(entry_time,1,10)=?",
                (day.isoformat(),),
            ).fetchone()
        return float(val or 0.0)

    def open_unrealized_pnl(self) -> float:
        # Unrealized must be computed by the paper engine using current quotes;
        # we expose a stored value via daily_metrics for a fast read.
        with self._conn() as c:
            row = c.execute(
                "SELECT unrealized_pnl_inr FROM daily_metrics ORDER BY day DESC LIMIT 1"
            ).fetchone()
        return float(row[0]) if row else 0.0

    # ---- signals ----

    def insert_signal(self, s: SignalSnapshot) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO signals(instrument, timestamp, raw_score, adjusted_score,
                    direction, layer_breakdown_json, macro_regime, macro_bias)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    s.instrument, s.timestamp.isoformat(), s.raw_score, s.adjusted_score,
                    s.direction, s.layer_breakdown_json, s.macro_regime, s.macro_bias,
                ),
            )
            return cur.lastrowid

    # ---- daily metrics ----

    def upsert_daily_metric(self, m: DailyMetric) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO daily_metrics(day, realized_pnl_inr, unrealized_pnl_inr, n_trades,
                    n_wins, n_losses, capital_inr, pnl_pct)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(day) DO UPDATE SET
                    realized_pnl_inr=excluded.realized_pnl_inr,
                    unrealized_pnl_inr=excluded.unrealized_pnl_inr,
                    n_trades=excluded.n_trades,
                    n_wins=excluded.n_wins,
                    n_losses=excluded.n_losses,
                    capital_inr=excluded.capital_inr,
                    pnl_pct=excluded.pnl_pct
                """,
                (m.day, m.realized_pnl_inr, m.unrealized_pnl_inr, m.n_trades,
                 m.n_wins, m.n_losses, m.capital_inr, m.pnl_pct),
            )

    @staticmethod
    def _row_to_trade(r: sqlite3.Row) -> TradeRecord:
        d = dict(r)
        d["entry_time"] = datetime.fromisoformat(d["entry_time"])
        if d.get("exit_time"):
            d["exit_time"] = datetime.fromisoformat(d["exit_time"])
        d["macro_aligned"] = bool(d["macro_aligned"])
        return TradeRecord(**d)
