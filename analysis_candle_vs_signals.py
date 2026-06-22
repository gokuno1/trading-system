"""
Analyze 5-minute NIFTY candles (yfinance) against signals stored in ledger.sqlite.
Find consecutive green/red candle streaks with significant movement,
then match each streak to the closest signal by timestamp.

Supports single-period analysis and pre-vs-post comparison mode.

Usage:
    python analysis_candle_vs_signals.py                          # defaults
    python analysis_candle_vs_signals.py 2026-06-08 2026-06-12    # custom range
    python analysis_candle_vs_signals.py --compare                # pre-fix vs post-fix
"""

import argparse
import json
import sqlite3
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

DB_PATH = Path(__file__).parent / "data" / "ledger" / "ledger.sqlite"
INSTRUMENT = "NIFTY"


# ── Helpers ──────────────────────────────────────────────────────────────────

def fetch_candles(start: str, end: str) -> pd.DataFrame:
    df = yf.download("^NSEI", start=start, end=end, interval="5m")
    if df.empty:
        raise SystemExit(f"No yfinance data for {start}–{end}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    ts_col = "Datetime" if "Datetime" in df.columns else "Date"
    df.rename(columns={ts_col: "datetime"}, inplace=True)
    df["datetime"] = pd.to_datetime(df["datetime"])
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df.sort_values("datetime", inplace=True)
    df.reset_index(drop=True, inplace=True)
    df["green"] = df["Close"] >= df["Open"]
    df["body"] = df["Close"] - df["Open"]
    return df


def load_signals(start: str, end: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT id, timestamp, raw_score, adjusted_score, direction, "
        "layer_breakdown_json, macro_regime, macro_bias "
        "FROM signals WHERE instrument = ? AND timestamp >= ? AND timestamp < ? "
        "ORDER BY timestamp",
        conn, params=(INSTRUMENT, start, end),
    )
    conn.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def find_streaks(df: pd.DataFrame, min_consec: int, min_pts: float) -> list:
    streaks = []
    i = 0
    while i < len(df):
        is_green = df.iloc[i]["green"]
        j = i
        while j < len(df) and df.iloc[j]["green"] == is_green:
            j += 1
        run_len = j - i
        if run_len >= min_consec:
            chunk = df.iloc[i:j]
            move = (chunk["Close"].iloc[-1] - chunk["Open"].iloc[0]) if is_green else (chunk["Open"].iloc[0] - chunk["Close"].iloc[-1])
            if abs(move) >= min_pts:
                streaks.append({
                    "type": "GREEN" if is_green else "RED",
                    "start_time": chunk["datetime"].iloc[0],
                    "end_time": chunk["datetime"].iloc[-1],
                    "candle_count": run_len,
                    "open_price": chunk["Open"].iloc[0],
                    "close_price": chunk["Close"].iloc[-1],
                    "high": chunk["High"].max(),
                    "low": chunk["Low"].min(),
                    "move_pts": move,
                })
        i = j
    return streaks


def find_all_streaks(candles: pd.DataFrame, min_consec: int, min_pts: float) -> list:
    all_streaks = []
    for _, day_df in candles.groupby(candles["datetime"].dt.date):
        all_streaks.extend(find_streaks(day_df.reset_index(drop=True), min_consec, min_pts))
    return all_streaks


def match_signals(streaks: list, signals_df: pd.DataFrame) -> list:
    results = []
    for streak in streaks:
        st, et = streak["start_time"], streak["end_time"]
        before = signals_df[(signals_df["timestamp"] >= st - timedelta(minutes=15)) & (signals_df["timestamp"] < st)]
        during = signals_df[(signals_df["timestamp"] >= st) & (signals_df["timestamp"] <= et)]
        after = signals_df[(signals_df["timestamp"] > et) & (signals_df["timestamp"] <= et + timedelta(minutes=15))]
        all_nearby = pd.concat([before, during, after]).drop_duplicates(subset=["id"])
        results.append({"streak": streak, "before": before, "during": during, "after": after, "all_nearby": all_nearby})
    return results


# ── Analysis for a single period ─────────────────────────────────────────────

def analyze_period(label: str, candles: pd.DataFrame, signals_df: pd.DataFrame,
                   min_consec: int, min_pts: float):
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")

    # Overview
    print(f"  Candles: {len(candles)} ({candles['datetime'].iloc[0].date()} to {candles['datetime'].iloc[-1].date()})")
    print(f"  Signals: {len(signals_df)}")
    dirs = signals_df["direction"].value_counts().to_dict()
    print(f"  Direction distribution: {dirs}")
    print(f"  Adjusted score: mean={signals_df['adjusted_score'].mean():.2f}, "
          f"min={signals_df['adjusted_score'].min():.2f}, max={signals_df['adjusted_score'].max():.2f}")

    # Detect streaks
    streaks = find_all_streaks(candles, min_consec, min_pts)
    print(f"\n  Streaks found ({min_consec}+ candles, {min_pts}+ pts): {len(streaks)}")
    for s in streaks:
        print(f"    {s['type']:5s} | {s['start_time']} → {s['end_time']} | "
              f"{s['candle_count']} candles | {s['move_pts']:+.1f} pts")

    # Match to signals
    matched = match_signals(streaks, signals_df)

    for i, m in enumerate(matched):
        s = m["streak"]
        sigs = m["all_nearby"]
        print(f"\n  ── Streak #{i+1}: {s['type']} ──")
        print(f"     Time:  {s['start_time']} → {s['end_time']}")
        print(f"     Move:  {s['move_pts']:+.1f} pts ({s['open_price']:.1f} → {s['close_price']:.1f})")

        if sigs.empty:
            print("     ⚠ NO SIGNALS found near this streak!")
            continue

        print(f"     Signals: {len(m['before'])} before | {len(m['during'])} during | {len(m['after'])} after")
        for _, sig in sigs.iterrows():
            layers = json.loads(sig["layer_breakdown_json"])
            rel = "BEFORE" if sig["timestamp"] < s["start_time"] else ("DURING" if sig["timestamp"] <= s["end_time"] else "AFTER")
            print(f"     [{rel:6s}] {sig['timestamp']}  direction={sig['direction']:8s}  "
                  f"adj={sig['adjusted_score']:+.1f}  raw={sig['raw_score']:+.1f}")
            for lk, lv in layers.items():
                print(f"              {lk}: {lv}")

    # Accuracy
    total = correct = neutral = wrong = 0
    for m in matched:
        expected = "long" if m["streak"]["type"] == "GREEN" else "short"
        for _, sig in m["all_nearby"].iterrows():
            total += 1
            if sig["direction"] == expected:
                correct += 1
            elif sig["direction"] == "neutral":
                neutral += 1
            else:
                wrong += 1

    if total:
        print(f"\n  Signal accuracy near strong moves:")
        print(f"    Total: {total} | Correct: {correct} ({correct/total*100:.1f}%) | "
              f"Neutral: {neutral} ({neutral/total*100:.1f}%) | Wrong: {wrong} ({wrong/total*100:.1f}%)")

    # Layer breakdown
    layer_data: dict[str, list[str]] = {}
    for m in matched:
        for _, sig in m["all_nearby"].iterrows():
            layers = json.loads(sig["layer_breakdown_json"])
            for lk, lv in layers.items():
                layer_data.setdefault(lk, []).append(lv)
    if layer_data:
        print(f"\n  Layer analysis (signals near strong moves):")
        for layer, notes in sorted(layer_data.items()):
            top = Counter(notes).most_common(3)
            top_str = " | ".join(f"[{c}x] {n[:60]}" for n, c in top)
            print(f"    {layer}: {top_str}")

    # Per-day overview
    print(f"\n  Per-day overview:")
    for day, ddf in candles.groupby(candles["datetime"].dt.date):
        o, h, l, c = ddf["Open"].iloc[0], ddf["High"].max(), ddf["Low"].min(), ddf["Close"].iloc[-1]
        g, r = int(ddf["green"].sum()), len(ddf) - int(ddf["green"].sum())
        day_sigs = signals_df[signals_df["timestamp"].dt.date == day]
        n_long = int((day_sigs["direction"] == "long").sum())
        n_neutral = int((day_sigs["direction"] == "neutral").sum())
        n_short = int((day_sigs["direction"] == "short").sum())
        print(f"    {day}: O={o:.0f} H={h:.0f} L={l:.0f} C={c:.0f} Range={h-l:.0f} "
              f"Change={c-o:+.0f} G/R={g}/{r} "
              f"Signals={len(day_sigs)} (long={n_long} neutral={n_neutral} short={n_short})")

    # Layer distribution for full period
    print(f"\n  Full-period layer distribution:")
    full_layer: dict[str, list[str]] = {}
    for _, sig in signals_df.iterrows():
        layers = json.loads(sig["layer_breakdown_json"])
        for lk, lv in layers.items():
            full_layer.setdefault(lk, []).append(lv)
    for layer, notes in sorted(full_layer.items()):
        top = Counter(notes).most_common(3)
        top_str = " | ".join(f"[{c}x/{c/len(notes)*100:.0f}%] {n[:55]}" for n, c in top)
        print(f"    {layer}: {top_str}")

    return {
        "signals": len(signals_df), "long": int(dirs.get("long", 0)),
        "neutral": int(dirs.get("neutral", 0)), "short": int(dirs.get("short", 0)),
        "streaks": len(streaks), "total_matched": total,
        "correct": correct, "neutral_matched": neutral, "wrong": wrong,
        "avg_adj": signals_df["adjusted_score"].mean(),
        "max_adj": signals_df["adjusted_score"].max(),
    }


# ── Comparison mode ──────────────────────────────────────────────────────────

def compare(pre_start, pre_end, post_start, post_end, min_consec, min_pts):
    print("Fetching candle data...")
    pre_candles = fetch_candles(pre_start, pre_end)
    post_candles = fetch_candles(post_start, post_end)

    pre_signals = load_signals(pre_start, pre_end)
    post_signals = load_signals(post_start, post_end)

    pre = analyze_period(f"PRE-FIX ({pre_start} to {pre_end})", pre_candles, pre_signals, min_consec, min_pts)
    post = analyze_period(f"POST-FIX ({post_start} to {post_end})", post_candles, post_signals, min_consec, min_pts)

    # Also scan with relaxed criteria for broader opportunity detection
    print(f"\n{'='*80}")
    print("BROADER OPPORTUNITY SCAN (3+ candles, 75+ pts)")
    print("=" * 80)
    broad_streaks = find_all_streaks(post_candles, min_consec=3, min_pts=75)
    print(f"  Found {len(broad_streaks)} broader opportunities in post-fix period:")
    for s in broad_streaks:
        print(f"    {s['type']:5s} | {s['start_time'].strftime('%b %d %H:%M')}–{s['end_time'].strftime('%H:%M')} | "
              f"{s['candle_count']} candles | {s['move_pts']:+.0f} pts")

    broad_matched = match_signals(broad_streaks, post_signals)
    bt = bc = bn = bw = 0
    for m in broad_matched:
        expected = "long" if m["streak"]["type"] == "GREEN" else "short"
        for _, sig in m["all_nearby"].iterrows():
            bt += 1
            if sig["direction"] == expected: bc += 1
            elif sig["direction"] == "neutral": bn += 1
            else: bw += 1
    if bt:
        print(f"\n  Accuracy near broader opportunities:")
        print(f"    Total: {bt} | Correct: {bc} ({bc/bt*100:.1f}%) | Neutral: {bn} ({bn/bt*100:.1f}%) | Wrong: {bw} ({bw/bt*100:.1f}%)")

    # Summary table
    print(f"\n{'='*80}")
    print("COMPARISON SUMMARY")
    print("=" * 80)
    print(f"  {'Metric':<35s} {'Pre-fix':>10s} {'Post-fix':>10s}")
    print(f"  {'-'*35} {'-'*10} {'-'*10}")
    print(f"  {'Total signals':<35s} {pre['signals']:>10d} {post['signals']:>10d}")
    print(f"  {'Long signals':<35s} {pre['long']:>10d} {post['long']:>10d}")
    print(f"  {'Neutral signals':<35s} {pre['neutral']:>10d} {post['neutral']:>10d}")
    print(f"  {'Avg adjusted score':<35s} {pre['avg_adj']:>10.2f} {post['avg_adj']:>10.2f}")
    print(f"  {'Max adjusted score':<35s} {pre['max_adj']:>10.2f} {post['max_adj']:>10.2f}")
    print(f"  {'Strong streaks detected':<35s} {pre['streaks']:>10d} {post['streaks']:>10d}")
    pre_pct = f"{pre['correct']/pre['total_matched']*100:.0f}%" if pre['total_matched'] else "N/A"
    post_pct = f"{post['correct']/post['total_matched']*100:.0f}%" if post['total_matched'] else "N/A"
    print(f"  {'Correct signals near streaks':<35s} {pre_pct:>10s} {post_pct:>10s}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("start", nargs="?", default="2026-06-08", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end", nargs="?", default="2026-06-12", help="End date exclusive (YYYY-MM-DD)")
    parser.add_argument("--compare", action="store_true", help="Run pre-fix vs post-fix comparison")
    parser.add_argument("--pre-start", default="2026-06-01", help="Pre-fix start date")
    parser.add_argument("--pre-end", default="2026-06-06", help="Pre-fix end date")
    parser.add_argument("--min-candles", type=int, default=5, help="Min consecutive candles")
    parser.add_argument("--min-pts", type=float, default=100, help="Min movement in points")
    args = parser.parse_args()

    if args.compare:
        compare(args.pre_start, args.pre_end, args.start, args.end, args.min_candles, args.min_pts)
    else:
        print("Fetching candle data...")
        candles = fetch_candles(args.start, args.end)
        signals = load_signals(args.start, args.end)
        analyze_period(f"Analysis ({args.start} to {args.end})", candles, signals, args.min_candles, args.min_pts)

    print("\nAnalysis complete.")


if __name__ == "__main__":
    main()
