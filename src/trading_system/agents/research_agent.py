"""Research agent — wraps the ``openalex-high-impact-research`` skill to
periodically validate the trading system's own edges against published
literature on options-flow, microstructure, and intraday strategies.

Cadence: monthly. The output is *informational*: it surfaces strategy edges
that have weakened (papers showing a previously-profitable signal has decayed)
or new edges worth A/B testing on paper. The intraday loop does not block on
the literature snapshot.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Dict


PROMPT_TEMPLATE = dedent("""
Run the ``openalex-high-impact-research`` skill with this query:

"What does the post-2018 high-impact literature say about exploitable edges in
intraday options markets — specifically: predictive value of put-call OI ratio,
max-pain gravitation, IV skew dynamics, and L2 order-book imbalance for
short-dated index options? Which previously-reported edges have decayed?"

Use the skill's full pipeline (retrieve → claims → hypotheses → contradictions →
score → structural fixes). When done, save the run document to MongoDB IF the
MongoDB MCP is available, then ALSO emit this trading-system snapshot:

```json
{snapshot_path}
```

Schema:

{{
  "generated_at": "<ISO UTC>",
  "edge_hypotheses": [
      "<short hypothesis grounded in cited works>", ...
  ],
  "contradictions": [
      "<contradicting findings, e.g. PCR predictive in some samples but not others>", ...
  ],
  "structural_interventions": [
      "<concrete change to the trading system's signal logic>", ...
  ],
  "notes": "<1-paragraph summary of what to test next>"
}}
""").strip()


def render_prompt(snapshots_dir: Path) -> str:
    out_path = snapshots_dir / "literature" / "{TODAY}.json"
    return PROMPT_TEMPLATE.format(snapshot_path=str(out_path))


def example_payload() -> Dict:
    return {
        "generated_at": "2026-04-30T03:30:00Z",
        "edge_hypotheses": [
            "Weighted L2 imbalance predicts 5-min returns out-of-sample but decays past 60s holding period.",
            "Max-pain gravitation strongest on expiry day after 13:00 IST.",
        ],
        "contradictions": [
            "PCR-OI signal flipped sign in post-2022 Indian samples vs pre-2018 US samples.",
        ],
        "structural_interventions": [
            "Down-weight Layer 4 PCR contribution outside the last 90 minutes of expiry day.",
            "Add a Layer 2 holding-period decay term to the score aggregator.",
        ],
        "notes": "Operate Layer 4 with reduced weight pre-1pm; preserve full weight in expiry-day final hour.",
    }
