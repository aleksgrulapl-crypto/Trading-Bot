# ============================
# TRADE LOG MODULE (CLEAN FORMAT)
# ============================

import json
import os
from utils import timestamp

LOG_FILE = "/data/trade_log.json"


# ---------------------------------------------------------
# SAFE LOAD
# ---------------------------------------------------------

def load_raw_log():
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[TRADE_LOG] Failed to load log: {e}")
        return []


# ---------------------------------------------------------
# SAFE SAVE
# ---------------------------------------------------------

def save_log(log):
    try:
        with open(LOG_FILE, "w") as f:
            json.dump(log, f, indent=4)
    except Exception as e:
        print(f"[TRADE_LOG] Failed to save log: {e}")


# ---------------------------------------------------------
# CLEAN TRADE FORMAT
# ---------------------------------------------------------

def clean_trade(entry):
    """
    Convert any incoming trade entry into the new clean format.
    """

    return {
        "dealId": entry.get("dealId"),
        "ticker": entry.get("ticker"),
        "side": entry.get("side").upper() if entry.get("side") else None,
        "size": float(entry.get("size")) if entry.get("size") else None,

        "time_entered": entry.get("time_entered") or entry.get("open_timestamp") or entry.get("time"),
        "time_exited": entry.get("time_exited") or entry.get("close_timestamp"),

        "entry_price": float(entry.get("entry_price")) if entry.get("entry_price") else None,
        "exit_price": float(entry.get("exit_price")) if entry.get("exit_price") else None,

        "pnl": float(entry.get("pnl")) if entry.get("pnl") else None,

        "status": entry.get("status") or ("OPEN" if entry.get("exit_price") in (None, "—") else "CLOSED")
    }


# ---------------------------------------------------------
# LOG OPEN TRADE
# ---------------------------------------------------------

def log_trade(ticker, epic, deal_id, side, size, price, sl, tp, timestamp, timeframe):
    log = load_raw_log()

    entry = {
        "dealId": deal_id,
        "ticker": ticker,
        "side": side,
        "size": size,
        "entry_price": price,
        "exit_price": None,
        "pnl": None,
        "time_entered": timestamp,
        "time_exited": None,
        "status": "OPEN"
    }

    log.append(clean_trade(entry))
    save_log(log)

    print(f"[TRADE_LOG] Logged OPEN trade → {ticker} {side} dealId={deal_id}")


# ---------------------------------------------------------
# LOG CLOSED TRADE
# ---------------------------------------------------------

def log_close(ticker, epic, deal_id, direction, size, entry_price, close_price, pnl, sl, tp, timestamp, timeframe):
    log = load_raw_log()

    # Remove any existing OPEN entry for this dealId
    log = [t for t in log if t.get("dealId") != deal_id]

    entry = {
        "dealId": deal_id,
        "ticker": ticker,
        "side": direction,
        "size": size,
        "entry_price": entry_price,
        "exit_price": close_price,
        "pnl": pnl,
        "time_entered": None,   # will be filled by merge
        "time_exited": timestamp,
        "status": "CLOSED"
    }

    log.append(clean_trade(entry))
    save_log(log)

    print(f"[TRADE_LOG] Logged CLOSED trade → {ticker} {direction} pnl={pnl}")


# ---------------------------------------------------------
# MERGE OPEN + CLOSED
# ---------------------------------------------------------

def merge_trades(log):
    """
    Combine OPEN and CLOSED entries into unified trades.
    """

    merged = {}
    for t in log:
        dealId = t.get("dealId")
        if not dealId:
            continue

        if dealId not in merged:
            merged[dealId] = t
        else:
            # Merge fields
            if t.get("entry_price"):
                merged[dealId]["entry_price"] = t["entry_price"]
            if t.get("exit_price"):
                merged[dealId]["exit_price"] = t["exit_price"]
            if t.get("pnl") is not None:
                merged[dealId]["pnl"] = t["pnl"]

            if t.get("time_entered"):
                merged[dealId]["time_entered"] = t["time_entered"]
            if t.get("time_exited"):
                merged[dealId]["time_exited"] = t["time_exited"]

            merged[dealId]["status"] = t.get("status", merged[dealId]["status"])

    return list(merged.values())
