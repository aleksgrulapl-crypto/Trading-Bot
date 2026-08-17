# ============================
# TRADE LOG CLEANER (FORMAT MIGRATION)
# ============================

import json
import os

LOG_FILE = "/data/trade_log.json"
BACKUP_FILE = "/data/trade_log_backup.json"

def load_log():
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[CLEANER] Failed to load log: {e}")
        return []

def save_log(log):
    try:
        with open(LOG_FILE, "w") as f:
            json.dump(log, f, indent=4)
    except Exception as e:
        print(f"[CLEANER] Failed to save log: {e}")

def backup_log():
    try:
        with open(LOG_FILE, "r") as f:
            data = f.read()
        with open(BACKUP_FILE, "w") as f:
            f.write(data)
        print(f"[CLEANER] Backup created at {BACKUP_FILE}")
    except Exception as e:
        print(f"[CLEANER] Backup failed: {e}")

def clean_entry(t):
    """
    Convert ANY old/malformed entry into the new clean format.
    """

    dealId = t.get("dealId") or t.get("trade_id") or t.get("id")

    ticker = t.get("ticker") or t.get("epic") or "UNKNOWN"
    side = t.get("side") or t.get("direction") or "BUY"
    side = side.upper()

    size = t.get("size") or t.get("qty") or None
    try:
        size = float(size) if size is not None else None
    except:
        size = None

    entry_price = t.get("entry_price") or t.get("price") or t.get("level")
    exit_price = t.get("exit_price") or t.get("closeLevel")

    try:
        entry_price = float(entry_price) if entry_price else None
    except:
        entry_price = None

    try:
        exit_price = float(exit_price) if exit_price else None
    except:
        exit_price = None

    pnl = t.get("pnl")
    try:
        pnl = float(pnl) if pnl is not None else None
    except:
        pnl = None

    time_entered = (
        t.get("time_entered")
        or t.get("open_timestamp")
        or t.get("time")
        or None
    )

    time_exited = (
        t.get("time_exited")
        or t.get("close_timestamp")
        or None
    )

    status = t.get("status")
    if not status:
        status = "OPEN" if exit_price is None else "CLOSED"

    return {
        "dealId": dealId,
        "ticker": ticker,
        "side": side,
        "size": size,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl": pnl,
        "time_entered": time_entered,
        "time_exited": time_exited,
        "status": status
    }

def clean_log():
    log = load_log()
    backup_log()

    cleaned = []
    seen = set()

    for t in log:
        dealId = t.get("dealId") or t.get("trade_id") or t.get("id")
        if not dealId:
            continue

        if dealId in seen:
            continue

        seen.add(dealId)
        cleaned.append(clean_entry(t))

    save_log(cleaned)
    print(f"[CLEANER] Cleaned {len(cleaned)} trades.")

if __name__ == "__main__":
    clean_log()
