"""Pure-Python option-chain analytics.

These are deterministic, side-effect-free, and unit-testable — they take a
parsed option chain and produce the structured features the microstructure
agent needs (PCR, max pain, OI walls, ATM band, IV skew).

Input shape (matches Upstox /v2/option/chain ``data`` array):
    [
      {
        "expiry": "2026-05-08",
        "strike_price": 22500,
        "underlying_spot_price": 22480.5,
        "call_options": {
            "instrument_key": "...",
            "market_data": {"ltp": 120.5, "oi": 1234500, "volume": 9000,
                            "bid_price": 119.75, "ask_price": 121.0},
            "option_greeks": {"iv": 13.2, "delta": 0.52}
        },
        "put_options": { ... }
      }, ...
    ]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class StrikeRow:
    strike: int
    ce_ltp: float
    pe_ltp: float
    ce_oi: float
    pe_oi: float
    ce_volume: float
    pe_volume: float
    ce_iv: Optional[float]
    pe_iv: Optional[float]
    ce_bid: float
    ce_ask: float
    pe_bid: float
    pe_ask: float


@dataclass
class OptionFeatures:
    spot: float
    expiry: str
    strikes: List[StrikeRow]
    pcr_oi: float
    pcr_volume: float
    max_pain: int
    highest_call_oi_strike: int
    highest_put_oi_strike: int
    atm_strike: int
    atm_straddle_price: float
    atm_iv: Optional[float]
    iv_skew_25d: Optional[float]
    expected_range_low: int
    expected_range_high: int

    def in_expected_range(self) -> bool:
        return self.expected_range_low <= self.spot <= self.expected_range_high


def _safe(v, default=0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def parse_chain(chain_payload: Dict) -> Tuple[float, str, List[StrikeRow]]:
    """Extract (spot, expiry, strike_rows) from the Upstox response."""

    rows: List[StrikeRow] = []
    spot = 0.0
    expiry = ""
    for entry in chain_payload.get("data", []):
        ce = entry.get("call_options", {}) or {}
        pe = entry.get("put_options", {}) or {}
        ce_md = ce.get("market_data", {}) or {}
        pe_md = pe.get("market_data", {}) or {}
        ce_g = ce.get("option_greeks", {}) or {}
        pe_g = pe.get("option_greeks", {}) or {}
        spot = _safe(entry.get("underlying_spot_price"), spot)
        expiry = entry.get("expiry", expiry)
        rows.append(
            StrikeRow(
                strike=int(entry.get("strike_price", 0)),
                ce_ltp=_safe(ce_md.get("ltp")),
                pe_ltp=_safe(pe_md.get("ltp")),
                ce_oi=_safe(ce_md.get("oi")),
                pe_oi=_safe(pe_md.get("oi")),
                ce_volume=_safe(ce_md.get("volume")),
                pe_volume=_safe(pe_md.get("volume")),
                ce_iv=_safe(ce_g.get("iv")) or None,
                pe_iv=_safe(pe_g.get("iv")) or None,
                ce_bid=_safe(ce_md.get("bid_price")),
                ce_ask=_safe(ce_md.get("ask_price")),
                pe_bid=_safe(pe_md.get("bid_price")),
                pe_ask=_safe(pe_md.get("ask_price")),
            )
        )
    rows.sort(key=lambda r: r.strike)
    return spot, expiry, rows


def compute_max_pain(rows: List[StrikeRow]) -> int:
    """Strike at which total OI value is minimized at expiry."""

    best_strike = rows[0].strike
    best_pain = float("inf")
    strikes = [r.strike for r in rows]
    for k in strikes:
        pain = 0.0
        for r in rows:
            if r.strike < k:
                pain += r.ce_oi * (k - r.strike)
            elif r.strike > k:
                pain += r.pe_oi * (r.strike - k)
        if pain < best_pain:
            best_pain = pain
            best_strike = k
    return best_strike


def _atm_index(rows: List[StrikeRow], spot: float) -> int:
    return min(range(len(rows)), key=lambda i: abs(rows[i].strike - spot))


def compute_features(chain_payload: Dict) -> OptionFeatures:
    spot, expiry, rows = parse_chain(chain_payload)
    if not rows:
        raise ValueError("empty option chain")

    total_call_oi = sum(r.ce_oi for r in rows) or 1.0
    total_put_oi = sum(r.pe_oi for r in rows) or 1.0
    total_call_vol = sum(r.ce_volume for r in rows) or 1.0
    total_put_vol = sum(r.pe_volume for r in rows) or 1.0
    pcr_oi = total_put_oi / total_call_oi
    pcr_volume = total_put_vol / total_call_vol

    highest_call_oi_strike = max(rows, key=lambda r: r.ce_oi).strike
    highest_put_oi_strike = max(rows, key=lambda r: r.pe_oi).strike

    atm_idx = _atm_index(rows, spot)
    atm = rows[atm_idx]
    atm_straddle = atm.ce_ltp + atm.pe_ltp
    atm_iv = None
    if atm.ce_iv is not None and atm.pe_iv is not None:
        atm_iv = (atm.ce_iv + atm.pe_iv) / 2

    # 25-delta IV skew proxy: pick rows ~5% OTM either side
    skew = None
    otm_call = next((r for r in rows[atm_idx:] if r.strike >= spot * 1.04), None)
    otm_put = next((r for r in reversed(rows[: atm_idx + 1]) if r.strike <= spot * 0.96), None)
    if otm_call and otm_put and otm_call.ce_iv and otm_put.pe_iv:
        skew = otm_put.pe_iv - otm_call.ce_iv

    max_pain = compute_max_pain(rows)
    expected_range_low = highest_put_oi_strike
    expected_range_high = highest_call_oi_strike
    if expected_range_low > expected_range_high:
        expected_range_low, expected_range_high = expected_range_high, expected_range_low

    return OptionFeatures(
        spot=spot,
        expiry=expiry,
        strikes=rows,
        pcr_oi=pcr_oi,
        pcr_volume=pcr_volume,
        max_pain=max_pain,
        highest_call_oi_strike=highest_call_oi_strike,
        highest_put_oi_strike=highest_put_oi_strike,
        atm_strike=atm.strike,
        atm_straddle_price=atm_straddle,
        atm_iv=atm_iv,
        iv_skew_25d=skew,
        expected_range_low=expected_range_low,
        expected_range_high=expected_range_high,
    )
