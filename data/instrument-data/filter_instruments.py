"""Filter NSE.json to keep only NIFTY 50 and BANKNIFTY instruments.

Outputs:
  nifty_banknifty.json — compact filtered data used by the trading system
      to resolve futures instrument keys for full_quote / order book depth.

Usage:
  python filter_instruments.py                    # uses NSE.json in same dir
  python filter_instruments.py /path/to/NSE.json  # explicit source path
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).parent
DEFAULT_SOURCE = SCRIPT_DIR / "NSE.json"
OUTPUT_PATH = SCRIPT_DIR / "nifty_banknifty.json"

INDEX_KEYS = {
    "NSE_INDEX|Nifty 50",
    "NSE_INDEX|Nifty Bank",
    "NSE_INDEX|Nifty IT",
    "NSE_INDEX|Nifty Fin Service",
}

FO_NAMES = {"NIFTY", "BANKNIFTY"}


def _matches(instrument: Dict[str, Any]) -> bool:
    seg = instrument.get("segment", "")
    if seg == "NSE_INDEX":
        return instrument.get("instrument_key") in INDEX_KEYS
    if seg == "NSE_FO":
        return instrument.get("name") in FO_NAMES
    return False


def filter_instruments(source: Path) -> Dict[str, Any]:
    with open(source) as f:
        all_instruments: List[Dict[str, Any]] = json.load(f)

    matched = [i for i in all_instruments if _matches(i)]

    index_entries = [i for i in matched if i["segment"] == "NSE_INDEX"]
    nifty_fut = [i for i in matched if i.get("name") == "NIFTY" and i.get("instrument_type") == "FUT"]
    nifty_ce = [i for i in matched if i.get("name") == "NIFTY" and i.get("instrument_type") == "CE"]
    nifty_pe = [i for i in matched if i.get("name") == "NIFTY" and i.get("instrument_type") == "PE"]
    bn_fut = [i for i in matched if i.get("name") == "BANKNIFTY" and i.get("instrument_type") == "FUT"]
    bn_ce = [i for i in matched if i.get("name") == "BANKNIFTY" and i.get("instrument_type") == "CE"]
    bn_pe = [i for i in matched if i.get("name") == "BANKNIFTY" and i.get("instrument_type") == "PE"]

    nifty_fut.sort(key=lambda i: i.get("expiry", 0))
    bn_fut.sort(key=lambda i: i.get("expiry", 0))

    output = {
        "generated_at": datetime.now().isoformat(),
        "source_file": source.name,
        "total_instruments": len(matched),
        "summary": {
            "indices": len(index_entries),
            "nifty_futures": len(nifty_fut),
            "nifty_options": len(nifty_ce) + len(nifty_pe),
            "banknifty_futures": len(bn_fut),
            "banknifty_options": len(bn_ce) + len(bn_pe),
        },
        "indices": index_entries,
        "nifty": {
            "futures": nifty_fut,
            "options_ce": nifty_ce,
            "options_pe": nifty_pe,
        },
        "banknifty": {
            "futures": bn_fut,
            "options_ce": bn_ce,
            "options_pe": bn_pe,
        },
    }
    return output


def nearest_futures_key(data: Dict[str, Any], symbol: str) -> Optional[str]:
    """Resolve the nearest-expiry futures instrument_key for NIFTY or BANKNIFTY.

    Returns the instrument_key (e.g. "NSE_FO|62329") of the front-month
    futures contract, which can be used with full_quote() and
    intraday_candles() to get real order book depth and volume data.
    """
    section = data.get(symbol.lower(), {})
    futures = section.get("futures", [])
    if not futures:
        return None
    now_ms = datetime.now().timestamp() * 1000
    active = [f for f in futures if f.get("expiry", 0) > now_ms]
    if not active:
        return futures[0].get("instrument_key")
    active.sort(key=lambda f: f["expiry"])
    return active[0].get("instrument_key")


def print_summary(data: Dict[str, Any]) -> None:
    s = data["summary"]
    print(f"Filtered {data['total_instruments']} instruments from {data['source_file']}")
    print(f"  Indices:            {s['indices']}")
    print(f"  NIFTY  futures:     {s['nifty_futures']}")
    print(f"  NIFTY  options:     {s['nifty_options']}")
    print(f"  BANKNIFTY futures:  {s['banknifty_futures']}")
    print(f"  BANKNIFTY options:  {s['banknifty_options']}")

    for sym in ("NIFTY", "BANKNIFTY"):
        fk = nearest_futures_key(data, sym)
        section = data.get(sym.lower(), {})
        fut = section.get("futures", [])
        nearest = next((f for f in fut if f.get("instrument_key") == fk), None)
        if nearest:
            exp_ts = nearest["expiry"] / 1000
            exp_str = datetime.fromtimestamp(exp_ts).strftime("%d %b %Y")
            print(f"\n  {sym} nearest futures:")
            print(f"    instrument_key:  {fk}")
            print(f"    trading_symbol:  {nearest['trading_symbol']}")
            print(f"    expiry:          {exp_str}")
            print(f"    lot_size:        {nearest['lot_size']}")

    print(f"\n  Index keys:")
    for idx in data["indices"]:
        print(f"    {idx['instrument_key']:35s}  {idx['name']}")


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    if not source.exists():
        print(f"Error: {source} not found", file=sys.stderr)
        sys.exit(1)

    data = filter_instruments(source)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Saved to {OUTPUT_PATH}")
    print_summary(data)


if __name__ == "__main__":
    main()
