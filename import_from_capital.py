#!/usr/bin/env python3
# import_from_capital.py
# Safe importer for Capital history/positions.
# Dry-run by default; use --apply to upsert/close trades via trade_log helpers.

import argparse
import json
import os
import shutil
from datetime import datetime
from typing import Dict, List, Any, Optional

LOG_PATH = os.environ.get("TRADE_LOG_PATH", "/data/trade_log.json")
BACKUP_DIR = os.path.dirname(LOG_PATH) or "/data"

# prefer using trade_log helpers for all writes
try:
    from trade_log import upsert_open_trade, close_trade_by_dealId, load_raw_log
except Exception:
    upsert_open_trade = None
    close_trade_by_dealId = None
    load_raw_log = None

try:
    import session
except Exception:
    session = None

def iso_or_none(s: Optional[str]) -> Optional[str]:
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
    # direction fallback: if explicit direction missing, try to infer
    side = pos.get("direction") or tx.get("side")
    if not side:
        # Capital sometimes uses "SELL" for short; default to Long if unclear
        side = "Short" if str(pos.get("direction","")).upper() == "SELL" else "Long"
    size = pos.get("size") or pos.get("contractSize") or tx.get("size") or 0
    entry_price = pos.get("level") or pos.get("entryPrice") or tx.get("entryPrice") or tx.get("level")
    exit_price = tx.get("price") or tx.get("closePrice") or tx.get("exitPrice") or None
    time_entered = pos.get("createdDate") or pos.get("createdDateUTC") or tx.get("time_entered")
    time_exited = tx.get("date") or tx.get("closedDate") or tx.get("timestamp") or None

    normalized = {
        "dealId": dealId,
        "dealReference": dealRef,
        "ticker": ticker,
        "side": side,
        "size": float(size or 0),
        "entry_price": float(entry_price) if entry_price not in (None, "") else None,
        "time_entered": iso_or_none(time_entered),
        "exit_price": float(exit_price) if exit_price not in (None, "") else None,
        "time_exited": iso_or_none(time_exited),
        "status": "CLOSED" if exit_price not in (None, "") else "OPEN",
        "raw": tx,
        "notes": "Imported from Capital history"
    }
    return normalized

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

def apply_normalized(normalized: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Upsert open trades and close closed trades using trade_log helpers.
    Returns counts: {'upserted': n, 'closed': m, 'skipped': k}
    """
    if upsert_open_trade is None or close_trade_by_dealId is None:
        raise RuntimeError("trade_log helpers not available")

    upserted = 0
    closed = 0
    skipped = 0

    # load existing for quick signature checks if needed
    existing = load_raw_log() if load_raw_log else []

    for n in normalized:
        # prefer to close by dealId if closed and dealId present
        if n.get("status") == "CLOSED" and n.get("dealId"):
            # ensure there is an existing open trade to close; attempt close regardless
            res = close_trade_by_dealId(n.get("dealId"), exit_price=n.get("exit_price"), time_exited=n.get("time_exited"), note="Imported from Capital history")
            if res:
                closed += 1
                continue
            # if close failed (no matching dealId), try upsert then close
        # Upsert open trades (will reject malformed payloads)
        upsert_payload = {
            "dealId": n.get("dealId"),
            "dealReference": n.get("dealReference"),
            "ticker": n.get("ticker"),
            "side": n.get("side"),
            "size": n.get("size"),
            "entry_price": n.get("entry_price"),
            "time_entered": n.get("time_entered"),
            "notes": n.get("notes")
        }
        up = upsert_open_trade(upsert_payload)
        if up:
            upserted += 1
            # if the normalized row is closed and upsert succeeded, close it
            if n.get("status") == "CLOSED":
                if up.get("dealId"):
                    res = close_trade_by_dealId(up.get("dealId"), exit_price=n.get("exit_price"), time_exited=n.get("time_exited"), note="Imported from Capital history")
                    if res:
                        closed += 1
                else:
                    # fallback: attempt to close by matching signature via close_trade_fallback if available
                    try:
                        from trade_log import close_trade_fallback
                        res = close_trade_fallback(up.get("ticker"), up.get("entry_price"), exit_price=n.get("exit_price"), time_exited=n.get("time_exited"), note="Imported from Capital history")
                        if res:
                            closed += 1
                    except Exception:
                        skipped += 1
        else:
            skipped += 1

    return {"upserted": upserted, "closed": closed, "skipped": skipped}

def main(dry_run: bool = True):
    txs = fetch_history()
    print(f"Fetched {len(txs)} history items")
    normalized = [normalize_tx(tx) for tx in txs]
    print("Sample normalized (first 5):")
    for n in normalized[:5]:
        print(json.dumps(n, indent=2, ensure_ascii=False))

    if dry_run:
        print("\nDry-run: no file changes. Re-run with --apply to import into trade_log via upsert/close helpers")
        return

    # apply: backup current log and apply upserts/closes
    bak = os.path.join(BACKUP_DIR, f"trade_log.importbak.{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json")
    try:
        if os.path.exists(LOG_PATH):
            shutil.copy2(LOG_PATH, bak)
            print("Backed up existing log to", bak)
    except Exception as e:
        print("Could not backup existing log; aborting:", e)
        return

    try:
        result = apply_normalized(normalized)
        print("Import result:", result)
    except Exception as e:
        print("Import failed:", e)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Upsert/close trades into trade_log via helpers")
    args = parser.parse_args()
    main(dry_run=not args.apply)
