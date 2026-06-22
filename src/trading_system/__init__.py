"""Agentic algorithmic trading system.

Phase 1: signal-only, paper-trade, NIFTY/BANKNIFTY options.

Layered design (highest cadence outside-in):
  - Macro Research Analyst skill (weekly): regime → directional bias
  - Micro Research Analyst skill (weekly): sector heavyweight quality
  - OpenAlex literature workflow (monthly): strategy edge validation
  - Microstructure Analyst (every 5 minutes during session): live trade card

Deterministic core (this package): scoring math, risk gates, position sizing,
paper-trade engine, ledger, reporting. The LLM-driven research stages run as
Cursor agent skills and write JSON snapshots into ``data/snapshots/`` which
this package reads.
"""

__version__ = "0.1.0"
