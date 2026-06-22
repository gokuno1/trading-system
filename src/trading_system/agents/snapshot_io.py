"""Snapshot I/O — agents write JSON files; the deterministic core reads them.

Layout:
    data/snapshots/macro/YYYY-MM-DD.json
    data/snapshots/micro/YYYY-MM-DD.json
    data/snapshots/literature/YYYY-MM-DD.json

The intraday loop loads the *latest* file in each folder (within its allowed
staleness window from the cadence config) and converts it into a typed
``MacroSnapshot`` / ``MicroSnapshot`` / ``LiteratureSnapshot`` for AgentState.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from ..state import LiteratureSnapshot, MacroSnapshot, MicroSnapshot

T = TypeVar("T", bound=BaseModel)


def _newest(dir_: Path) -> Optional[Path]:
    if not dir_.exists():
        return None
    files = sorted(dir_.glob("*.json"))
    return files[-1] if files else None


def write_snapshot(snapshots_dir: Path, kind: str, payload: BaseModel) -> Path:
    sub = snapshots_dir / kind
    sub.mkdir(parents=True, exist_ok=True)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    path = sub / f"{today}.json"
    path.write_text(payload.model_dump_json(indent=2))
    return path


def _load(snapshots_dir: Path, kind: str, max_age_days: int, cls: Type[T]) -> Optional[T]:
    sub = snapshots_dir / kind
    p = _newest(sub)
    if not p:
        return None
    age = datetime.utcnow() - datetime.fromtimestamp(p.stat().st_mtime)
    if age > timedelta(days=max_age_days):
        return None
    raw = json.loads(p.read_text())
    return cls(**raw)


def load_macro(snapshots_dir: Path, max_age_days: int = 7) -> Optional[MacroSnapshot]:
    return _load(snapshots_dir, "macro", max_age_days, MacroSnapshot)


def load_micro(snapshots_dir: Path, max_age_days: int = 7) -> Optional[MicroSnapshot]:
    return _load(snapshots_dir, "micro", max_age_days, MicroSnapshot)


def load_literature(snapshots_dir: Path, max_age_days: int = 30) -> Optional[LiteratureSnapshot]:
    return _load(snapshots_dir, "literature", max_age_days, LiteratureSnapshot)
