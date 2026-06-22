"""Shared state passed between LangGraph nodes.

Each agent reads/writes specific fields. Treat all fields as optional during
construction; nodes populate them as the graph progresses. Down-stream nodes
must defensively check for ``None`` and short-circuit if a required upstream
input is missing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["long", "short", "neutral"]
Verdict = Literal["bullish", "lean_bullish", "neutral", "lean_bearish", "bearish"]


class MacroSnapshot(BaseModel):
    """Output of the macro research skill, persisted as JSON in snapshots/."""

    generated_at: datetime
    regime: str
    confidence: Literal["high", "moderate", "low"]
    dominant_force: str
    counter_signals: List[str] = Field(default_factory=list)
    sector_tilts: Dict[str, Verdict] = Field(default_factory=dict)
    nifty_directional_bias: Verdict
    banknifty_directional_bias: Verdict
    notes: str = ""

    @property
    def is_stale(self, max_days: int = 7) -> bool:
        return (datetime.utcnow() - self.generated_at).days > max_days


class MicroSnapshot(BaseModel):
    """Output of the micro research skill for index sector heavyweights."""

    generated_at: datetime
    sector_quality: Dict[str, Verdict]
    heavyweight_drag: List[str] = Field(default_factory=list)
    heavyweight_pull: List[str] = Field(default_factory=list)
    notes: str = ""


class LiteratureSnapshot(BaseModel):
    """Output of the OpenAlex high-impact research skill."""

    generated_at: datetime
    edge_hypotheses: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    structural_interventions: List[str] = Field(default_factory=list)
    notes: str = ""


class MicrostructureScore(BaseModel):
    """Output of the microstructure agent for a single instrument."""

    instrument: str
    timestamp: datetime
    layer1_book_structure: float
    layer2_order_flow: float
    layer3_liquidity: float
    layer4_option_chain: float
    layer5_vwap_volume: float
    layer6_cross_market: float
    layer7_context_multiplier: float
    layer8_candle_momentum: float = 0.0
    raw_score: float
    adjusted_score: float
    direction: Direction
    layer_notes: Dict[str, str] = Field(default_factory=dict)
    raw_data: Dict[str, Any] = Field(default_factory=dict, exclude=True)


class TradeCard(BaseModel):
    """Final actionable signal emitted to the operator."""

    instrument: str
    direction: Direction
    underlying_ltp: float
    selected_strike: int
    option_type: Literal["CE", "PE"]
    expiry: str

    entry_price: float
    stop_loss: float
    target_1: float
    target_2: Optional[float] = None
    reward_risk: float

    position_size_lots: int
    risk_amount_inr: float
    notional_inr: float

    adjusted_score: float
    macro_aligned: bool

    invalidation_conditions: List[str] = Field(default_factory=list)
    layer_breakdown: Dict[str, str] = Field(default_factory=dict)
    notes: str = ""


class RiskGateResult(BaseModel):
    passed: bool
    reasons_rejected: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    daily_loss_pct: float = 0.0
    open_positions: int = 0


class AgentState(BaseModel):
    """The single object that flows through the LangGraph pipeline."""

    # Inputs
    instrument: str
    now_ist: datetime

    # Snapshots loaded from disk
    macro: Optional[MacroSnapshot] = None
    micro: Optional[MicroSnapshot] = None
    literature: Optional[LiteratureSnapshot] = None

    # Cached baselines (populated once in load_snapshots, zero hot-path cost)
    expected_volume_daily: Optional[float] = None
    futures_instrument_key: Optional[str] = None

    # Rolling WBI history for L1 smoothing (populated by microstructure agent)
    wbi_history: List[float] = Field(default_factory=list)

    # Computed within the graph
    microstructure: Optional[MicrostructureScore] = None
    risk_gate: Optional[RiskGateResult] = None
    trade_card: Optional[TradeCard] = None

    # Bookkeeping
    errors: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)

    def log(self, msg: str) -> None:
        self.decisions.append(msg)

    def fail(self, msg: str) -> None:
        self.errors.append(msg)
