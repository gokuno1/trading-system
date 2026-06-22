"""Typed configuration loader.

Reads three YAML files from ``config/`` and merges them into a single
``SystemConfig`` Pydantic model. All knobs the system uses at runtime
are surfaced here — never read YAML directly elsewhere.
"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from typing import List, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


def _ist_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class CapitalConfig(BaseModel):
    total_inr: float
    notes: str = ""

    @field_validator("total_inr")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("capital must be > 0")
        return v


class CadenceConfig(BaseModel):
    macro_refresh_days: int = 7
    micro_refresh_days: int = 7
    literature_refresh_days: int = 30
    intraday_loop_seconds: int = 300
    pre_market_run_time_ist: str = "08:30"
    eod_run_time_ist: str = "15:30"

    @property
    def pre_market_time(self) -> time:
        return _ist_time(self.pre_market_run_time_ist)

    @property
    def eod_time(self) -> time:
        return _ist_time(self.eod_run_time_ist)


class InstrumentConfig(BaseModel):
    symbol: str
    upstox_instrument_key: str
    related_keys: List[str] = Field(default_factory=list)
    lot_size: int
    expiry_cadence: Literal["weekly", "monthly"] = "weekly"
    expiry_weekday: str = "Tuesday"


class PathsConfig(BaseModel):
    snapshots: Path
    ledger_db: Path
    reports: Path

    def ensure(self) -> None:
        self.snapshots.mkdir(parents=True, exist_ok=True)
        self.ledger_db.parent.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)


class PerTradeRisk(BaseModel):
    max_risk_pct: float
    max_notional_pct: float
    max_premium_pct: float
    min_reward_risk: float


class DailyRisk(BaseModel):
    max_loss_pct: float
    max_concurrent_positions: int


class SessionRules(BaseModel):
    no_entry_before_ist: str
    no_entry_after_ist: str
    flatten_all_by_ist: str
    expiry_day_size_multiplier: float
    rvol_floor_for_trade: float

    @property
    def no_entry_before(self) -> time:
        return _ist_time(self.no_entry_before_ist)

    @property
    def no_entry_after(self) -> time:
        return _ist_time(self.no_entry_after_ist)

    @property
    def flatten_all_by(self) -> time:
        return _ist_time(self.flatten_all_by_ist)


class ScoreGates(BaseModel):
    microstructure_min_abs_score: float
    macro_alignment_required: bool = False
    macro_contradiction_penalty: float = 1.0


class StopLossConfig(BaseModel):
    max_atr_multiple: float
    hard_max_atr_multiple: float


class RiskConfig(BaseModel):
    profile: str
    per_trade: PerTradeRisk
    daily: DailyRisk
    session_rules: SessionRules
    score_gates: ScoreGates
    stop_loss: StopLossConfig


class InstrumentMath(BaseModel):
    option_strike_step: dict[str, int]
    preferred_strikes: dict
    iv_envelope: dict
    liquidity_filters: dict


class SystemConfig(BaseModel):
    environment: str
    mode: str
    timezone: str
    capital: CapitalConfig
    cadences: CadenceConfig
    instruments: List[InstrumentConfig]
    paths: PathsConfig
    risk: RiskConfig
    instrument_math: InstrumentMath


def load_config(config_dir: Path | str = "config") -> SystemConfig:
    """Load and validate the three YAML files into a ``SystemConfig``.

    Environment variable ``TRADING_CAPITAL_INR`` overrides ``capital.total_inr``
    so operators can run the same config with different capital tiers.
    """

    config_dir = Path(config_dir)
    sys_y = yaml.safe_load((config_dir / "system.yaml").read_text())
    risk_y = yaml.safe_load((config_dir / "risk.yaml").read_text())
    inst_y = yaml.safe_load((config_dir / "instruments.yaml").read_text())

    capital_override = os.environ.get("TRADING_CAPITAL_INR")
    if capital_override:
        sys_y["capital"]["total_inr"] = float(capital_override)

    cfg = SystemConfig(
        environment=sys_y["environment"],
        mode=sys_y["mode"],
        timezone=sys_y["timezone"],
        capital=CapitalConfig(**sys_y["capital"]),
        cadences=CadenceConfig(**sys_y["cadences"]),
        instruments=[InstrumentConfig(**i) for i in sys_y["instruments"]],
        paths=PathsConfig(**{k: Path(v) for k, v in sys_y["paths"].items()}),
        risk=RiskConfig(**risk_y),
        instrument_math=InstrumentMath(**inst_y),
    )
    cfg.paths.ensure()
    return cfg
