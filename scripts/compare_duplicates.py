#!/usr/bin/env python3
"""
compare_duplicates.py

Find and print potential duplicate trade_log entries side-by-side for quick inspection.

Usage:
  python compare_duplicates.py          # dry-run, prints duplicates if any
  python compare_duplicates.py --json   # prints duplicates as JSON for machine parsing
"""

import json
import argparse
from collections import defaultdict
from typing import Any, Dict, List

LOG_PATH = "trade_log.json"  # adjust if you use a different path


def load_trades(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except FileNotFoundError:
        print(f"[ERROR] {path} not found")
        return []
    except Exception as e:
        print(f"[ERROR] Failed to load {path}: {e}")
        return []


def key_for_group(t: Dict[str, Any]) -> str:
    """
    Grouping key: ticker + rounded entry_price (2 decimals) + side.
    This groups entries that are likely the same logical trade.
    """
    ticker = (t.get("ticker") or "").upper()
    try:
        entry = float(t.get("entry_price") or 0.0)
        entry_r = round(entry, 2)
    except Exception:
        entry_r = t.get("entry_price")
    side = (t.get("side") or "").lower()
    return f"{ticker}|{entry_r}|{side}"


def pretty_print_pair(a: Dict[str, Any], b: Dict[str, Any], idx_a: int, idx_b: int) -> None:
    fields = [
        ("dealId", ""),
        ("dealReference", ""),
        ("ticker", ""),
        ("side", ""),
        ("size", ""),
        ("entry_price", ""),
        ("exit_price", ""),
        ("time_entered", ""),
        ("time_exited", ""),
        ("pnl", ""),
        ("status", ""),
        ("notes", "")
    ]

    print("=" * 100)
    print(f"Duplicate pair: log_index_a={idx_a}  log_index_b={idx_b}")
    print("-" * 100)
    # header
    print(f"{'FIELD':30} | {'ENTRY A':35} | {'ENTRY B':35}")
    print("-" * 100)
    for key, _ in fields:
        va = a.get(key)
        vb = b.get(key)
        # stringify safely
        try:
            sa = json.dumps(va, ensure_ascii=False)
        except Exception:
            sa = str(va)
        try:
            sb = json.dumps(vb, ensure_ascii=False)
        except Exception:
            sb = str(vb)
        print(f"{key:30} | {sa[:35]:35} | {sb[:35]:35}")
    print("=" * 100)
    print()


def find_duplicates(trades: List[Dict[str, Any]]) -> List[tuple]:
    groups = defaultdict(list)
    for idx, t in enumerate(trades):
        k = key_for_group(t)
        groups[k].append((idx, t))

    duplicates = []
    for k, items in groups.items():
        if len(items) > 1:
            # produce all unique pairs
            n = len(items)
            for i in range(n):
                for j in range(i + 1, n):
                    duplicates.append((items[i], items[j]))
    return duplicates


def main(as_json: bool = False):
    trades = load_trades(LOG_PATH)
    if not trades:
        print("[INFO] No trades found in trade_log.json")
        return

    duplicates = find_duplicates(trades)
    if not duplicates:
        print("[OK] No obvious duplicates found by (ticker, entry_price, side) grouping.")
        return

    print(f"[FOUND] {len(duplicates)} duplicate pair(s) found\n")
    if as_json:
        out = []
        for (i_a, a), (i_b, b) in duplicates:
            out.append({
                "index_a": i_a,
                "index_b": i_b,
                "entry_a": a,
                "entry_b": b
            })
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    for (i_a, a), (i_b, b) in duplicates:
        pretty_print_pair(a, b, i_a, i_b)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare duplicate trade_log entries side-by-side")
    parser.add_argument("--json", action="store_true", help="Output duplicates as JSON")
    args = parser.parse_args()
    main(as_json=args.json)
