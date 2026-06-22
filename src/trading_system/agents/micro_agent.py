"""Micro agent — wraps the ``micro-research-analyst`` skill, scoped to the
sector heavyweights that drive NIFTY 50 and BANKNIFTY price action.

For an index-only trader the relevant micro question is *"are the heavyweights
acting as a tailwind or drag?"*, not deep DCFs on every name. This module
emits a focused prompt that asks the skill to run its 9-dimension framework
on the top 5 contributors to each index and roll up to a sector quality map.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Dict


HEAVYWEIGHTS = {
    "NIFTY": ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "TCS", "BHARTIARTL"],
    "BANKNIFTY": ["HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN", "KOTAKBANK"],
}


PROMPT_TEMPLATE = dedent("""
You are running the ``micro-research-analyst`` skill on the sector
heavyweights that drive NIFTY 50 and BANKNIFTY.

Stocks to cover:
  NIFTY heavyweights:    {nifty_list}
  BANKNIFTY heavyweights: {banknifty_list}

For each stock, run the 9-dimension framework but bias depth toward
*near-term earnings/guidance and pricing-power evidence over the last 90 days*
since this snapshot is used for swing/intraday index decisions, not 3-year
investment views.

Aggregate by sector:
  - banks  (HDFCBANK, ICICIBANK, AXISBANK, SBIN, KOTAKBANK)
  - it     (INFY, TCS, HCLTECH, WIPRO, TECHM)
  - fmcg   (ITC, HINDUNILVR, NESTLEIND, TATACONSUM)
  - energy (RELIANCE, ONGC, COALINDIA)
  - auto   (M&M, MARUTI, TATAMOTORS, EICHERMOT)

For each sector, give a single quality verdict in:
  bullish | lean_bullish | neutral | lean_bearish | bearish

Then emit the JSON snapshot:

```json
{snapshot_path}
```

Schema:

{{
  "generated_at": "<ISO UTC>",
  "sector_quality": {{
      "banks": "...",
      "it": "...",
      "fmcg": "...",
      "energy": "...",
      "auto": "..."
  }},
  "heavyweight_drag": ["<ticker that materially drags its sector>", ...],
  "heavyweight_pull": ["<ticker that materially pulls its sector up>", ...],
  "notes": "<short narrative of the dominant micro story for this week>"
}}

CRITICAL: do not invent earnings numbers. If no recent data exists for a
heavyweight, mark its sector verdict as ``neutral`` and note the gap.
""").strip()


def render_prompt(snapshots_dir: Path) -> str:
    out_path = snapshots_dir / "micro" / "{TODAY}.json"
    return PROMPT_TEMPLATE.format(
        nifty_list=", ".join(HEAVYWEIGHTS["NIFTY"]),
        banknifty_list=", ".join(HEAVYWEIGHTS["BANKNIFTY"]),
        snapshot_path=str(out_path),
    )


def example_payload() -> Dict:
    return {
        "generated_at": "2026-04-30T03:30:00Z",
        "sector_quality": {
            "banks": "lean_bullish",
            "it": "lean_bearish",
            "fmcg": "neutral",
            "energy": "neutral",
            "auto": "lean_bullish",
        },
        "heavyweight_drag": ["INFY"],
        "heavyweight_pull": ["HDFCBANK", "M&M"],
        "notes": "Banks beating estimates on NIM; IT guidance cut for FY27 weighing on the index.",
    }
