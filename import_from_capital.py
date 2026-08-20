#!/usr/bin/env python3
# import_from_capital.py
# Importer for Capital history/positions. Dry-run by default; use --apply to overwrite trade_log.json.
# Produces normalized entries compatible with trade_log.py.

import argparse
import json
import os
import shutil
from datetime import datetime
from typing import Dict, List, Any

LOG_PATH = os.environ.get("TRADE_LOG_PATH", "/data/trade_log.json")
BACKUP_DIR = os.path.dirname(LOG_PATH) or "/data"
FX_USD_GBP = float(os.environ.get("FX_USD_GBP", "0.78"))

try:
    import session
except Exception:
    session = None

def iso_or_none(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.isoformat()
    except Exception:
        return s

def normalize_tx(tx: Dict[str, Any]) -> Dict[str, Any]:
    pos = tx.get("position") or tx
    market = tx.get("market") or tx
    dealId = pos.get("dealId") or pos.get("id") or tx.get("dealId")
    dealRef = pos.get("dealReference") or pos.get("deal_reference") or tx.get("dealReference")
    ticker = market.get("symbol") or market.get("epic") or tx.get("symbol") or tx.get("instrument")
    side = pos.get("direction") or tx.get("side") or ("Short" if str(pos.get("direction","")).upper()=="SELL" else "Long")
    size = pos.get("size") or pos.get("contractSize") or tx.get("size") or 0
    entry_price = pos.get("level") or pos.get("entryPrice") or tx.get("entryPrice") or tx.get("level")
    exit_price = tx.get("price") or tx.get("closePrice") or tx.get("exitPrice") or None
    time_entered = pos.get("createdDate") or pos.get("createdDateUTC") or tx.get("time_entered")
    time_exited = tx.get("date") or tx.get("closedDate") or tx.get("timestamp") or None

    entry = {
        "dealId": dealId,
        "dealReference": dealRef,
        "ticker": ticker,
        "side": side,
        "size": float(size or 0),
        "entry_price": float(entry_price) if entry_price not in (None,"") else None,
        "time_entered": iso_or_none(time_entered),
        "exit_price": float(exit_price) if exit_price not in (None,"") else None,
        "time_exited": iso_or_none(time_exited),
        "status": "CLOSED" if exit_price else "OPEN",
        "notes": "Imported from Capital history"
    }
    try:
        if entry["status"] == "CLOSED" and entry["entry_price"] not in (None, "") and entry["exit_price"] not in (None, ""):
            if entry["side"].lower() in ("long","buy"):
                pnl = round((entry["exit_price"] - entry["entry_price"]) * entry["size"], 2)
            else:
                pnl = round((entry["entry_price"] - entry["exit_price"]) * entry["size"], 2)
            entry["pnl"] = pnl
            entry["pnl_gbp"] = round(pnl * FX_USD_GBP, 2)
    except Exception:
        entry["pnl"] = None
        entry["pnl_gbp"] = None

    return entry

def fetch_history() -> List[Dict[str, Any]]:
    candidates = []
    try:
        import config
        if getattr(config, "API_HISTORY_TRANSACTIONS", None):
            candidates.append(config.API_HISTORY_TRANSACTIONS)
        if getattr(config, "API_BASE", None):
            base = config.API_BASE.rstrip("/")
            candidates.append(f"{base}/api/v1/history/transactions?max=500")
            candidates.append(f"{base}/api/v1/history/positions?max=500")
    except Exception:
        pass

    if session and hasattr(session, "request"):
        for url in candidates:
            try:
                r = session.request("GET", url)
                if not r:
                    continue
                try:
                    body = r.json()
                except Exception:
                    body = json.loads(r.text)
                if isinstance(body, dict):
                    for k in ("transactions","items","positions","history"):
                        if k in body and isinstance(body[k], list):
                            return body[k]
                    for v in body.values():
                        if isinstance(v, list):
                            return v
                elif isinstance(body, list):
                    return body
            except Exception:
                continue
    return []

def main(dry_run: bool = True):
    txs = fetch_history()
    print(f"Fetched {len(txs)} history items")
    normalized = [normalize_tx(tx) for tx in txs]
    print("Sample normalized (first 5):")
    for n in normalized[:5]:
        print(json.dumps(n, indent=2, ensure_ascii=False))
    if dry_run:
        print("\nDry-run: no file changes. Re-run with --apply to write trade_log.json")
        return

    bak = os.path.join(BACKUP_DIR, f"trade_log.importbak.{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json")
    try:
        if os.path.exists(LOG_PATH):
            shutil.copy2(LOG_PATH, bak)
            print("Backed up existing log to", bak)
    except Exception:
        print("Could not backup existing log; aborting")
        return

    try:
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
        print("Wrote new trade_log.json with", len(normalized), "entries")
    except Exception as e:
        print("Failed to write new log:", e)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write normalized history to trade_log.json")
    args = parser.parse_args()
    main(dry_run=not args.apply)
