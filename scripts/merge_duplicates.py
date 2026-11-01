#!/usr/bin/env python3
"""
merge_duplicates.py

Detect and merge likely duplicate trade_log entries.

Rules (deterministic):
- Group by (ticker.upper(), round(entry_price, 2), side.lower()).
- Score each entry:
    +10 if time_entered contains 'T' (ISO style with timezone)
    + 8 if dealId present
    + 6 if exit_price present
    + 4 if status == 'CLOSED'
    + 2 for later time_exited (compared within group)
- Choose entry with highest score as canonical.
- Merge notes (append), preserve canonical fields, and remove other entries.
- Backup original trade_log.json to trade_log.json.mergebak.TIMESTAMP before writing.

Usage:
    python3 merge_duplicates.py        # dry-run, prints planned changes
    python3 merge_duplicates.py --apply  # apply changes (writes trade_log.json)
"""

import json
import argparse
import os
from datetime import datetime
from collections import defaultdict
from typing import Any, Dict, List, Tuple

LOG_PATH = os.environ.get("TRADE_LOG_PATH", "trade_log.json")
BACKUP_SUFFIX = ".mergebak."

def load_trades(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        print(f"[ERROR] {path} not found")
        return []
    except Exception as e:
        print(f"[ERROR] Failed to load {path}: {e}")
        return []

def save_trades(path: str, trades: List[Dict[str, Any]]) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(trades, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save {path}: {e}")
        return False

def key_for_group(t: Dict[str, Any]) -> Tuple[str, float, str]:
    ticker = (t.get("ticker") or "").upper()
    try:
        entry = float(t.get("entry_price") or 0.0)
        entry_r = round(entry, 2)
    except Exception:
        entry_r = float(0.0)
    side = (t.get("side") or "").lower()
    return (ticker, entry_r, side)

def parse_time(s):
    if not s:
        return None
    try:
        # try ISO first
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        # try common formats
        for fmt in ("%Y-%m-%d %H.%M.%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return None

def score_entry(t: Dict[str, Any], group_max_time_exited) -> int:
    score = 0
    te = t.get("time_entered") or ""
    if "T" in str(te):
        score += 10
    if t.get("dealId"):
        score += 8
    if t.get("exit_price") not in (None, "", 0):
        score += 6
    if (t.get("status") or "").upper() == "CLOSED":
        score += 4
    # later time_exited gets small bonus
    te_out = parse_time(t.get("time_exited"))
    if te_out and group_max_time_exited:
        try:
            if te_out == group_max_time_exited:
                score += 2
        except Exception:
            pass
    return score

def merge_group(entries: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Return (canonical_entry, removed_entries)
    """
    # compute group max time_exited
    times = [parse_time(e.get("time_exited")) for e in entries if e.get("time_exited")]
    group_max = max([t for t in times if t is not None], default=None)

    scored = []
    for e in entries:
        s = score_entry(e, group_max)
        scored.append((s, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    canonical = scored[0][1]

    # Merge notes and prefer fields from canonical, but fill missing fields from others
    merged = dict(canonical)  # shallow copy
    notes = merged.get("notes") or ""
    for _, e in scored[1:]:
        # append notes
        n = e.get("notes")
        if n:
            if notes:
                notes = notes + " | " + n
            else:
                notes = n
        # fill missing fields if canonical lacks them
        for key in ("dealId", "dealReference", "exit_price", "time_exited", "size", "entry_price", "time_entered", "status"):
            if merged.get(key) in (None, "", 0):
                if e.get(key) not in (None, "", 0):
                    merged[key] = e.get(key)
    merged["notes"] = notes or merged.get("notes")
    # recompute pnl if exit_price present
    try:
        if merged.get("exit_price") not in (None, "", 0) and merged.get("entry_price") not in (None, "", 0):
            entry = float(merged.get("entry_price"))
            exitp = float(merged.get("exit_price"))
            size = float(merged.get("size") or 0)
            side = (merged.get("side") or "").lower()
            if side in ("long", "buy"):
                pnl = round((exitp - entry) * size, 2)
            else:
                pnl = round((entry - exitp) * size, 2)
            merged["pnl"] = pnl
    except Exception:
        pass

    removed = [e for _, e in scored[1:]]
    return merged, removed

def main(apply: bool = False):
    trades = load_trades(LOG_PATH)
    if not trades:
        print("[INFO] No trades found.")
        return

    groups = defaultdict(list)
    for idx, t in enumerate(trades):
        k = key_for_group(t)
        groups[k].append((idx, t))

    planned_changes = []
    for k, items in groups.items():
        if len(items) <= 1:
            continue
        # extract only the trade dicts
        entries = [t for idx, t in items]
        canonical, removed = merge_group(entries)
        planned_changes.append({
            "group_key": k,
            "canonical": canonical,
            "removed_count": len(removed),
            "removed_examples": removed[:3]
        })

    if not planned_changes:
        print("[OK] No duplicate groups detected.")
        return

    print(f"[FOUND] {len(planned_changes)} duplicate group(s) detected.")
    for i, c in enumerate(planned_changes, 1):
        print(f"\nGroup {i}: key={c['group_key']}  will remove {c['removed_count']} entry(ies).")
        print("Canonical (summary): dealId=%s dealRef=%s size=%s entry=%s time_entered=%s time_exited=%s status=%s" % (
            c["canonical"].get("dealId"),
            c["canonical"].get("dealReference"),
            c["canonical"].get("size"),
            c["canonical"].get("entry_price"),
            c["canonical"].get("time_entered"),
            c["canonical"].get("time_exited"),
            c["canonical"].get("status"),
        ))
        print("Removed examples (first up to 3):")
        for r in c["removed_examples"]:
            print("  - idx dealId=%s dealRef=%s size=%s entry=%s time_entered=%s time_exited=%s status=%s" % (
                r.get("dealId"), r.get("dealReference"), r.get("size"), r.get("entry_price"), r.get("time_entered"), r.get("time_exited"), r.get("status")
            ))

    if not apply:
        print("\nDry-run complete. Re-run with --apply to perform the merges (a backup will be created).")
        return

    # APPLY changes
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = LOG_PATH + BACKUP_SUFFIX + ts
    try:
        os.rename(LOG_PATH, backup_path)
        print(f"[INFO] Backed up original log to {backup_path}")
    except Exception as e:
        print(f"[ERROR] Failed to backup {LOG_PATH}: {e}")
        return

    # rebuild trades list: iterate groups and replace merged canonical, skip removed
    # Build a set of removed signatures to skip
    removed_set = set()
    merged_entries = {}
    for c in planned_changes:
        key = c["group_key"]
        canonical = c["canonical"]
        # create a signature for removed entries to skip them
        for r in c["removed_examples"]:
            sig = json.dumps(r, sort_keys=True)
            removed_set.add(sig)
        # store canonical by key
        merged_entries[key] = canonical

    # Reconstruct new_trades: iterate original trades, if trade belongs to a merged group, include canonical once and skip others
    new_trades = []
    seen_group_included = set()
    for t in trades:
        k = key_for_group(t)
        if k in merged_entries:
            if k in seen_group_included:
                # skip duplicates
                continue
            # include canonical
            new_trades.append(merged_entries[k])
            seen_group_included.add(k)
        else:
            new_trades.append(t)

    # Save
    ok = save_trades(LOG_PATH, new_trades)
    if ok:
        print(f"[APPLY] Merged duplicates and wrote {LOG_PATH}.")
        print(f"[INFO] Removed duplicates for {len(planned_changes)} group(s). Backup at {backup_path}")
    else:
        # attempt to restore backup
        try:
            os.rename(backup_path, LOG_PATH)
            print("[ERROR] Save failed; restored backup.")
        except Exception:
            print("[CRITICAL] Save failed and backup restore failed. Manual recovery required.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge duplicate trade_log entries")
    parser.add_argument("--apply", action="store_true", help="Apply changes (write trade_log.json)")
    args = parser.parse_args()
    main(apply=args.apply)
