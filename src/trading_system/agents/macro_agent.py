"""Macro agent — wraps the ``macro-research-analyst`` Cursor skill.

The Python program does NOT call an LLM. Instead, this module emits a fully
formed prompt that the operator pastes into a Cursor agent session. The agent
runs the skill (7 macro dimensions, regime classification, sector mapping)
and is instructed to write its final output as a JSON file conforming to
:class:`trading_system.state.MacroSnapshot` into
``data/snapshots/macro/YYYY-MM-DD.json``.

Cadence: weekly (or on regime-changing news events). The intraday loop
refuses to operate without a macro snapshot newer than ``macro_refresh_days``.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Dict


PROMPT_TEMPLATE = dedent("""
You are running the ``macro-research-analyst`` skill for the Indian markets.

Goal: produce a macro regime call and directional bias for NIFTY 50 and
BANKNIFTY for the next 1–7 trading days, then SAVE THE RESULT as a JSON
snapshot file used by the trading system.

Process (follow the skill — do not skip phases):
1. Cover ALL 7 mandatory macro dimensions with India-specific queries:
   - RBI monetary policy (repo rate, liquidity, GSAP/OMO)
   - GoI fiscal policy (capex, fiscal deficit, GST collections)
   - Inflation (CPI, WPI, food/core split)
   - Labour market (PMI employment subindex, EPFO data, unemployment)
   - Growth (GDP nowcasts, IIP, core sector, PMI)
   - Credit conditions (bank credit growth, NPA trends, AA spreads)
   - Geopolitical & global (Fed path, US 10y, oil, USD/INR, FII flows)
2. Print the coverage tracker after each pass. Do NOT proceed below the
   coverage gate (zero gaps, ≤1 partial).
3. Synthesize a macro regime label + India-specific dominant force.
4. Map the regime to:
   - NIFTY directional bias (bullish/lean_bullish/neutral/lean_bearish/bearish)
   - BANKNIFTY directional bias (same vocabulary)
   - Sector tilts for the heavyweights (Banks, IT, FMCG, Energy, Auto)

Then emit the JSON file:

```json
{snapshot_path}
```

with this exact schema:

{{
  "generated_at": "<ISO 8601 UTC, e.g. 2026-04-30T11:00:00Z>",
  "regime": "<one of: goldilocks, reflation, stagflation, late_cycle, recession, early_recovery>",
  "confidence": "<high|moderate|low>",
  "dominant_force": "<one short sentence>",
  "counter_signals": ["<signal that contradicts the regime call>", ...],
  "sector_tilts": {{
      "banks": "bullish|lean_bullish|neutral|lean_bearish|bearish",
      "it": "...",
      "fmcg": "...",
      "energy": "...",
      "auto": "..."
  }},
  "nifty_directional_bias": "bullish|lean_bullish|neutral|lean_bearish|bearish",
  "banknifty_directional_bias": "...",
  "notes": "<3–6 lines: regime narrative, key inflection risks, dates to watch>"
}}

CRITICAL: this snapshot drives the intraday trade gating. Do not invent data.
Do not output without the coverage gate passing.
""").strip()


def render_prompt(snapshots_dir: Path) -> str:
    out_path = snapshots_dir / "macro" / "{TODAY}.json"
    return PROMPT_TEMPLATE.format(snapshot_path=str(out_path))


def example_payload() -> Dict:
    """Fallback hand-edited example for tests / first-run bootstrap."""

    return {
        "generated_at": "2026-04-30T03:30:00Z",
        "regime": "late_cycle",
        "confidence": "moderate",
        "dominant_force": "Sticky core inflation forcing RBI to hold while growth cools.",
        "counter_signals": [
            "FII flows have turned net buyer in last 5 sessions",
            "Q4 corporate margins surprised positively",
        ],
        "sector_tilts": {
            "banks": "neutral",
            "it": "lean_bearish",
            "fmcg": "lean_bullish",
            "energy": "neutral",
            "auto": "lean_bullish",
        },
        "nifty_directional_bias": "neutral",
        "banknifty_directional_bias": "lean_bullish",
        "notes": "Range-bound regime; expect 22300–22800 NIFTY range until next CPI print on May 12.",
    }
