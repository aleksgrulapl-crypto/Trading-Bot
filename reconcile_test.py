#!/usr/bin/env python3
"""
reconcile_test.py

Reconciliation helper:
- Reports live positions that are not present in trade_log (match order: dealId -> dealReference -> ticker+entry_price)
- Reports closed transactions in history that are not reflected in trade_log (missing exit_price/time_exited)
- Optionally applies fixes when run with --apply:
    * set dealId for entries that were logged with dealReference only
    * close trades by dealId or fallback by ticker+entry_price when exit info is found

Usage:
    python3 reconcile_test.py                # dry-run, prints findings
    python3 reconcile_test.py --apply        # apply fixes to trade_log.json
    python3 reconcile_test.py --path /data/trade_log.json --apply
"""

import argparse
import json
import os
import shutil
from typing import Any, Dict, List, Optional

# Default path (change with --path)
DEFAULT_LOG_PATH = "/data/trade_log.json"

# Import local modules if available
try:
    from trade_log import load_raw_log, append_open_trade, set_dealId_for_dealReference, close_trade_by_dealId, close_trade_fallback, save_raw_log
except Exception:
    # Fallback implementations if trade_log module not importable
    def load_raw_log(path=DEFAULT_LOG_PATH):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_raw_log(trades, path=DEFAULT_LOG_PATH):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(trades, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False

    def append_open_trade(payload):
        trades = load_raw_log()
        trades.append(payload)
        save_raw_log(trades)
        return payload

    def set_dealId_for_dealReference(dealReference, dealId):
        trades = load_raw_log()
        updated = False
        for t in trades:
            if t.get("dealReference") == dealReference and not t.get("dealId"):
                t["dealId"] = dealId
                t["notes"] = (t.get("notes") or "") + f" | dealId_mapped={dealId}"
                updated = True
                break
        if updated:
            save_raw_log(trades)
        return updated

    def close_trade_by_dealId(dealId, exit_price=None, time_exited=None, note=None):
        trades = load_raw_log()
        updated = None
        for t in trades:
            if t.get("dealId") and str(t.get("dealId")) == str(dealId) and t.get("status") != "CLOSED":
                if exit_price is not None:
                    t["exit_price"] = exit_price
                t["time_exited"] = time_exited or t.get("time_exited") or None
                t["status"] = "CLOSED"
                if note:
                    t["notes"] = (t.get("notes") or "") + " | " + note
                updated = t
                break
        if updated:
            save_raw_log(trades)
        return updated

    def close_trade_fallback(ticker, entry_price, exit_price=None, time_exited=None, note=None):
        trades = load_raw_log()
        updated = None
        for t in trades:
            if t.get("status") != "CLOSED" and t.get("ticker") == ticker:
                try:
                    if float(t.get("entry_price", 0)) == float(entry_price):
                        if exit_price is not None:
                            t["exit_price"] = exit_price
                        t["time_exited"] = time_exited or t.get("time_exited") or None
                        t["status"] = "CLOSED"
                        if note:
                            t["notes"] = (t.get("notes") or "") + " | " + note
                        updated = t
                        break
                except Exception:
                    continue
        if updated:
            save_raw_log(trades)
        return updated

# Session wrapper to call your session.request/get_positions/history endpoints
try:
    import session
except Exception:
    session = None

def fetch_live_positions() -> List[Dict[str, Any]]:
    """Return enriched live positions if session.get_positions exists, else try reading from API endpoints."""
    if session and hasattr(session, "get_positions"):
        try:
            return session.get_positions() or []
        except Exception:
            pass
    # fallback: no live positions available
    return []

def fetch_history_transactions() -> List[Dict[str, Any]]:
    """
    Try to fetch recent history transactions from configured endpoints.
    If session.request exists and config.API_BASE is available, attempt to call history endpoints.
    """
    txs = []
    # Try session.request if available
    try:
        import config
        if session and hasattr(session, "request"):
            candidates = []
            if getattr(config, "API_HISTORY_TRANSACTIONS", None):
                candidates.append(config.API_HISTORY_TRANSACTIONS)
            if getattr(config, "API_BASE", None):
                base = config.API_BASE.rstrip("/")
                candidates.extend([
                    f"{base}/api/v1/history/transactions?max=500",
                    f"{base}/api/v1/history/activity?max=500",
                    f"{base}/api/v1/history/positions?max=500",
                ])
            for url in candidates:
                try:
                    r = session.request("GET", url)
                    if not r:
                        continue
                    try:
                        body = r.json() or {}
                    except Exception:
                        try:
                            body = json.loads(r.text)
                        except Exception:
                            body = {}
                    if isinstance(body, dict):
                        for key in ("transactions", "items", "activity", "history"):
                            if key in body and isinstance(body[key], list):
                                txs = body[key]
                                break
                        if not txs:
                            # try to find any list inside
                            for v in body.values():
                                if isinstance(v, list):
                                    txs = v
                                    break
                    elif isinstance(body, list):
                        txs = body
                    if txs:
                        return txs
                except Exception:
                    continue
    except Exception:
        pass
    return txs

def _make_signature(dealId: Any, dealReference: Any, ticker: Any, entry_price: Any) -> str:
    try:
        entry_norm = round(float(entry_price or 0), 8)
    except Exception:
        entry_norm = str(entry_price)
    return f"{dealId or ''}|{dealReference or ''}|{ticker or ''}|{entry_norm}"

def build_log_maps(trades: List[Dict[str, Any]]):
    by_dealId = {}
    by_dealRef = {}
    by_signature = {}
    for t in trades:
        did = t.get("dealId")
        dref = t.get("dealReference")
        sig = _make_signature(did, dref, t.get("ticker"), t.get("entry_price"))
        by_signature[sig] = t
        if did:
            by_dealId[str(did)] = t
        if dref:
            by_dealRef[str(dref)] = t
    return by_dealId, by_dealRef, by_signature

def find_matching_log_entry(trades_maps, dealId: Optional[str], dealRef: Optional[str], ticker: Optional[str], entry_price: Optional[float]):
    by_dealId, by_dealRef, by_signature = trades_maps
    if dealId and str(dealId) in by_dealId:
        return by_dealId[str(dealId)], "dealId"
    if dealRef and str(dealRef) in by_dealRef:
        return by_dealRef[str(dealRef)], "dealReference"
    sig = _make_signature(dealId, dealRef, ticker, entry_price)
    if sig in by_signature:
        return by_signature[sig], "signature"
    # fallback: try tolerant numeric match on entry_price + ticker
    for s, t in by_signature.items():
        try:
            if (t.get("ticker") == ticker) and abs(float(t.get("entry_price", 0)) - float(entry_price or 0)) <= 1e-6:
                return t, "tolerance"
        except Exception:
            continue
    return None, None

def extract_exit_info_from_tx(tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Try to extract dealId, dealReference, exit_price, time_exited, ticker, entry_price from a history transaction.
    """
    dealId = tx.get("dealId") or tx.get("deal_id") or tx.get("positionId")
    dealRef = tx.get("dealReference") or tx.get("deal_reference")
    exit_price = tx.get("price") or tx.get("closePrice") or tx.get("exitPrice") or tx.get("closedPrice")
    time_exited = tx.get("date") or tx.get("createdDate") or tx.get("closedDate") or tx.get("timestamp")
    ticker = tx.get("symbol") or tx.get("instrument") or tx.get("epic")
    entry_price = tx.get("entryPrice") or tx.get("openPrice") or tx.get("level")
    # nested search
    for k in ("transaction", "data", "details", "position", "market"):
        nested = tx.get(k)
        if isinstance(nested, dict):
            exit_price = exit_price or nested.get("price") or nested.get("closePrice")
            time_exited = time_exited or nested.get("date") or nested.get("closedDate")
            dealId = dealId or nested.get("dealId")
            dealRef = dealRef or nested.get("dealReference")
            ticker = ticker or nested.get("symbol") or nested.get("instrument")
            entry_price = entry_price or nested.get("entryPrice") or nested.get("level")
    if exit_price is None and time_exited is None:
        return None
    return {
        "dealId": dealId,
        "dealReference": dealRef,
        "exit_price": exit_price,
        "time_exited": time_exited,
        "ticker": ticker,
        "entry_price": entry_price,
        "raw": tx
    }

def main(log_path: str, apply_changes: bool):
    print("=== Reconciliation test ===")
    trades = load_raw_log() if 'load_raw_log' in globals() and callable(load_raw_log) else []
    # If load_raw_log from module expects no args, use it; else try reading file
    if not trades:
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except Exception:
            trades = []

    print(f"Loaded {len(trades)} trades from {log_path}")

    trades_maps = build_log_maps(trades)

    live_positions = fetch_live_positions()
    print(f"Fetched {len(live_positions)} live positions")

    unmatched_live = []
    for p in live_positions:
        dealId = p.get("dealId") or p.get("id")
        dealRef = p.get("dealReference") or p.get("deal_reference")
        ticker = p.get("ticker") or p.get("epic") or p.get("symbol")
        entry_price = p.get("price") or p.get("entry_price") or p.get("level")
        match, how = find_matching_log_entry(trades_maps, dealId, dealRef, ticker, entry_price)
        if not match:
            unmatched_live.append({
                "dealId": dealId,
                "dealReference": dealRef,
                "ticker": ticker,
                "entry_price": entry_price,
                "raw": p
            })

    if unmatched_live:
        print(f"\n[UNMATCHED LIVE POSITIONS] {len(unmatched_live)} positions not found in trade_log:")
        for u in unmatched_live:
            print(json.dumps(u, default=str))
    else:
        print("\n[OK] All live positions matched to trade_log entries")

    txs = fetch_history_transactions()
    closes_found = []
    for tx in txs:
        info = extract_exit_info_from_tx(tx)
        if not info:
            continue
        if info.get("exit_price") is None and info.get("time_exited") is None:
            continue
        match, how = find_matching_log_entry(trades_maps, info.get("dealId"), info.get("dealReference"), info.get("ticker"), info.get("entry_price"))
        if match:
            if match.get("status") != "CLOSED" or match.get("exit_price") in (None, "", 0):
                closes_found.append({
                    "history": info,
                    "log_entry": match,
                    "match_by": how
                })
        else:
            closes_found.append({
                "history": info,
                "log_entry": None,
                "match_by": None
            })

    if closes_found:
        print(f"\n[CLOSES FOUND IN HISTORY] {len(closes_found)} close records that may need to be applied to trade_log:")
        for c in closes_found:
            print("----")
            print("History:", json.dumps({
                "dealId": c["history"].get("dealId"),
                "dealReference": c["history"].get("dealReference"),
                "ticker": c["history"].get("ticker"),
                "entry_price": c["history"].get("entry_price"),
                "exit_price": c["history"].get("exit_price"),
                "time_exited": c["history"].get("time_exited")
            }, default=str))
            if c["log_entry"]:
                print("Matched log entry (status, entry_price, size):", json.dumps({
                    "status": c["log_entry"].get("status"),
                    "entry_price": c["log_entry"].get("entry_price"),
                    "size": c["log_entry"].get("size"),
                    "dealId": c["log_entry"].get("dealId"),
                    "dealReference": c["log_entry"].get("dealReference")
                }, default=str))
            else:
                print("No matching log entry found for this close.")
    else:
        print("\n[OK] No close records found in history that are missing from trade_log")

    if not apply_changes:
        print("\nDry-run complete. No changes applied. Re-run with --apply to apply fixes.")
        return

    # APPLY changes
    print("\n=== Applying fixes ===")
    # backup
    bak = f"{log_path}.reconcilebak.{os.getenv('USER','user')}.{os.path.basename(log_path)}.{os.urandom(4).hex()}"
    try:
        shutil.copy2(log_path, bak)
        print(f"[INFO] Backed up original log to {bak}")
    except Exception as e:
        print("[WARN] Could not create backup:", e)

    # a) add unmatched live positions
    if unmatched_live:
        print(f"Adding {len(unmatched_live)} live positions to trade_log")
        for u in unmatched_live:
            payload = {
                "dealId": u.get("dealId"),
                "dealReference": u.get("dealReference"),
                "ticker": u.get("ticker"),
                "side": u.get("raw").get("direction") if isinstance(u.get("raw"), dict) else None,
                "size": u.get("raw").get("size") if isinstance(u.get("raw"), dict) else None,
                "entry_price": u.get("entry_price"),
                "time_entered": u.get("raw").get("time_entered") if isinstance(u.get("raw"), dict) else None,
                "exit_price": None,
                "time_exited": None,
                "pnl": None,
                "status": "OPEN",
                "notes": "Imported from live positions (reconcile_test)"
            }
            append_open_trade(payload)
            print("Appended live position:", u.get("dealReference") or u.get("dealId"))

    # b) apply closes found in history
    if closes_found:
        for c in closes_found:
            hist = c["history"]
            log_entry = c["log_entry"]
            exit_price = hist.get("exit_price")
            time_exited = hist.get("time_exited")
            dealId = hist.get("dealId")
            dealRef = hist.get("dealReference")
            ticker = hist.get("ticker")
            entry_price = hist.get("entry_price")

            if log_entry:
                if log_entry.get("dealId"):
                    res = close_trade_by_dealId(log_entry.get("dealId"), exit_price=exit_price, time_exited=time_exited, note="Applied from history")
                    print("Closed by dealId:", log_entry.get("dealId"), "result:", bool(res))
                elif dealRef:
                    if dealId:
                        updated = set_dealId_for_dealReference(dealRef, dealId)
                        print("Mapped dealReference -> dealId:", dealRef, "->", dealId, "updated:", updated)
                        if updated:
                            res = close_trade_by_dealId(dealId, exit_price=exit_price, time_exited=time_exited, note="Applied from history")
                            print("Closed after mapping dealId:", dealId, "result:", bool(res))
                    else:
                        res = close_trade_fallback(ticker, entry_price, exit_price=exit_price, time_exited=time_exited, note="Applied from history")
                        print("Closed by fallback (ticker+entry):", ticker, entry_price, "result:", bool(res))
                else:
                    res = close_trade_fallback(ticker, entry_price, exit_price=exit_price, time_exited=time_exited, note="Applied from history")
                    print("Closed by fallback (ticker+entry):", ticker, entry_price, "result:", bool(res))
            else:
                print("No log entry for history close. Creating a closed record in trade_log.")
                payload = {
                    "dealId": dealId,
                    "dealReference": dealRef,
                    "ticker": ticker,
                    "side": None,
                    "size": 0,
                    "entry_price": entry_price or 0,
                    "time_entered": None,
                    "exit_price": exit_price,
                    "time_exited": time_exited,
                    "pnl": None,
                    "status": "CLOSED",
                    "notes": "Imported closed trade from history (reconcile_test)"
                }
                append_open_trade(payload)
                print("Created closed record for history item.")

    print("=== Fixes applied. Please inspect trade_log.json ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconciliation test for trade_log vs live positions/history")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to trade_log (set dealId, close trades)")
    parser.add_argument("--path", default=DEFAULT_LOG_PATH, help="Path to trade_log.json")
    args = parser.parse_args()
    main(args.path, args.apply)
