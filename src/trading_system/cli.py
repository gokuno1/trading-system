"""Typer CLI — single entry point for all scheduled jobs.

Subcommands map 1:1 to the scripts under ``scripts/`` and to cron entries:

    trading-system pre-market         (08:30 IST, weekdays)
    trading-system intraday           (loop every 5 min, 09:25–15:10 IST)
    trading-system intraday-once      (single shot — for cron / debugging)
    trading-system eod                (15:30 IST)
    trading-system print-prompt macro
    trading-system print-prompt micro
    trading-system print-prompt research
    trading-system bootstrap-snapshots (writes example_payload() snapshots
                                        so the system can run without LLM yet)
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import pytz
import typer
from rich import print as rprint

from .agents import macro_agent, micro_agent, research_agent
from .agents.snapshot_io import write_snapshot
from .config import load_config
from .data.upstox import UpstoxClient
from .orchestrator import run_pipeline
from .paper_trade.engine import PaperEngine
from .persistence.store import Store
from .reporting.daily_report import build_daily_report, render_daily_text
from .reporting.trade_card import format_no_trade, format_trade_card
from .state import LiteratureSnapshot, MacroSnapshot, MicroSnapshot

app = typer.Typer(help="Agentic algorithmic trading system — signal-only NIFTY/BANKNIFTY options.")

IST = pytz.timezone("Asia/Kolkata")


def _now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


@app.command("pre-market")
def pre_market(config_dir: str = "config") -> None:
    """Print a checklist for the pre-market window."""

    cfg = load_config(config_dir)
    macro = (cfg.paths.snapshots / "macro").glob("*.json")
    micro = (cfg.paths.snapshots / "micro").glob("*.json")
    rprint("[bold]Pre-market checklist[/bold]")
    rprint(f"  Capital:           ₹{cfg.capital.total_inr:,.0f}")
    rprint(f"  Macro snapshots:   {len(list(macro))}")
    rprint(f"  Micro snapshots:   {len(list(micro))}")
    rprint("Run these if any are missing or stale:")
    rprint("  trading-system print-prompt macro")
    rprint("  trading-system print-prompt micro")


@app.command("print-prompt")
def print_prompt(kind: str, config_dir: str = "config") -> None:
    """Emit the agent prompt for ``macro`` | ``micro`` | ``research``."""

    cfg = load_config(config_dir)
    if kind == "macro":
        rprint(macro_agent.render_prompt(cfg.paths.snapshots))
    elif kind == "micro":
        rprint(micro_agent.render_prompt(cfg.paths.snapshots))
    elif kind == "research":
        rprint(research_agent.render_prompt(cfg.paths.snapshots))
    else:
        raise typer.BadParameter("kind must be macro|micro|research")


@app.command("bootstrap-snapshots")
def bootstrap(config_dir: str = "config") -> None:
    """Write example snapshots so the rest of the system can run without an LLM."""

    cfg = load_config(config_dir)
    write_snapshot(cfg.paths.snapshots, "macro", MacroSnapshot(**macro_agent.example_payload()))
    write_snapshot(cfg.paths.snapshots, "micro", MicroSnapshot(**micro_agent.example_payload()))
    write_snapshot(
        cfg.paths.snapshots, "literature", LiteratureSnapshot(**research_agent.example_payload())
    )
    rprint("[green]Bootstrapped example snapshots into[/green] " + str(cfg.paths.snapshots))


@app.command("intraday-once")
def intraday_once(config_dir: str = "config") -> None:
    """Run the pipeline once for every configured instrument and exit."""

    cfg = load_config(config_dir)
    client = UpstoxClient()
    store = Store(cfg.paths.ledger_db)
    for inst in cfg.instruments:
        rprint(f"[bold]→ {inst.symbol}[/bold]")
        state = run_pipeline(cfg, inst, client, store)
        if state.trade_card:
            rprint(format_trade_card(state.trade_card, state.microstructure))
        else:
            rprint(format_no_trade(state))
        for line in state.decisions:
            rprint(f"  · {line}")
        for err in state.errors:
            rprint(f"  [red]ERR:[/red] {err}")


@app.command("intraday")
def intraday_loop(config_dir: str = "config") -> None:
    """Loop every ``intraday_loop_seconds`` until 15:10 IST."""

    cfg = load_config(config_dir)
    client = UpstoxClient()
    store = Store(cfg.paths.ledger_db)
    while True:
        now = _now_ist()
        if now.time() < cfg.risk.session_rules.no_entry_before:
            sleep_s = 30
        elif now.time() > cfg.risk.session_rules.no_entry_after:
            rprint("[yellow]Past no-entry window — exiting intraday loop.[/yellow]")
            break
        else:
            for inst in cfg.instruments:
                state = run_pipeline(cfg, inst, client, store)
                if state.trade_card:
                    rprint(format_trade_card(state.trade_card, state.microstructure))
                elif state.errors:
                    for err in state.errors:
                        rprint(f"[red]{inst.symbol} ERR:[/red] {err}")
                else:
                    reasons = ", ".join(state.risk_gate.reasons_rejected) if state.risk_gate else "unknown"
                    rprint(f"[dim]{inst.symbol}: no trade — {reasons}[/dim]")
            sleep_s = cfg.cadences.intraday_loop_seconds
        time.sleep(sleep_s)


@app.command("eod")
def eod(config_dir: str = "config") -> None:
    """Generate end-of-day report and force-flatten any lingering paper positions."""

    cfg = load_config(config_dir)
    store = Store(cfg.paths.ledger_db)
    engine = PaperEngine(store)
    flattened = engine.force_flatten_all(current_prices={})  # use entry as fallback
    rprint(f"Flattened {len(flattened)} open paper position(s).")
    metric = build_daily_report(store, date.today(), cfg.capital.total_inr)
    rprint(render_daily_text(metric))


@app.command("status")
def status(config_dir: str = "config") -> None:
    """Quick health check."""

    cfg = load_config(config_dir)
    store = Store(cfg.paths.ledger_db)
    open_n = store.count_open_positions()
    realized = store.daily_realized_pnl(date.today())
    rprint(json.dumps({
        "capital_inr": cfg.capital.total_inr,
        "open_positions": open_n,
        "realized_today_inr": realized,
        "snapshots_dir": str(cfg.paths.snapshots),
    }, indent=2))


if __name__ == "__main__":  # pragma: no cover
    app()
