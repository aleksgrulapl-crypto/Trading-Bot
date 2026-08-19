# ============================
# TRADE LOG MODULE (UNIFIED OPEN/CLOSED ROWS + RAW LOAD)
# ============================

import json
import os
from datetime import datetime
import config

LOG_FILE = config.TRADE_LOG_FILE

# Ensure directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------
# RAW LOAD (for dashboard)
# ---------------------------------------------------------

def load_raw_log():
    """
    Load the raw trade log from disk.
    Used by dashboard for normalization/dedupe.
    """
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[TRADE_LOG] Failed to load log: {e}")
        return []


# ---------------------------------------------------------
# SAFE SAVE (atomic)
# ---------------------------------------------------------

def save_log(log):
    """
    Save the trade log using atomic write.
    """
    tmp_file = LOG_FILE + ".tmp"

    try:
        with open(tmp_file, "w") as f:
            json.dump(log, f, indent=4)
        os.replace(tmp_file, LOG_FILE)
    except Exception as e:
        print(f"[TRADE_LOG] Failed to save log: {e}")


# ---------------------------------------------------------
# LOG OPEN TRADE (UNIFIED FORMAT)
# ---------------------------------------------------------

def log_open_trade(
    ticker,
    epic,
    deal_id,
    side,
    size,
    entry_price,
    sl=None,
    tp=None,
    timeframe=None,
    timestamp=None,
):
    """
    Log an OPEN trade in unified format.
    Separate row, not updated later.
    """
    log = load_raw_log()

    entry = {
        "dealId": str(deal_id) if deal_id else None,
        "ticker": ticker or epic,
        "epic": epic,
        "side": str(side).upper() if side else None,
        "size": float(size) if size is not None else None,

        "entry_price": float(entry_price) if entry_price is not None else None,
        "exit_price": None,
        "pnl": None,

        "time_entered": timestamp or _now(),
        "time_exited": None,
        "status": "OPEN",

        "sl": sl,
        "tp": tp,
        "timeframe": timeframe,
    }

    log.append(entry)
    save_log(log)

    print(
        f"[TRADE_LOG] Logged OPEN trade → {ticker} {side} size={size} dealId={deal_id}",
        flush=True,
    )


# ---------------------------------------------------------
# LOG CLOSED TRADE (UNIFIED FORMAT)
# ---------------------------------------------------------

def log_closed_trade(
    ticker,
    epic,
    deal_id,
    side,
    size,
    entry_price,
    exit_price,
    pnl,
    sl=None,
    tp=None,
    timeframe=None,
    time_entered=None,
    timestamp=None,
):
    """
    Log a CLOSED trade in unified format.
Separate row, not merged with OPEN.
    """
    log = load_raw_log()

    entry = {
        "dealId": str(deal_id) if deal_id else None,
        "ticker": ticker or epic,
        "epic": epic,
        "side": str(side).upper() if side else "CLOSE",
        "size": float(size) if size is not None else None,

        "entry_price": float(entry_price) if entry_price is not None else None,
        "exit_price": float(exit_price) if exit_price is not None else None,
        "pnl": float(pnl) if pnl is not None else None,

        "time_entered": time_entered,
        "time_exited": timestamp or _now(),
        "status": "CLOSED",

        "sl": sl,
        "tp": tp,
        "timeframe": timeframe,
    }

    log.append(entry)
    save_log(log)

    print(
        f"[TRADE_LOG] Logged CLOSED trade → {ticker} CLOSE size={size} pnl={pnl}",
        flush=True,
    )

# ---------------------------------------------------------
# RESET LOG (WIPE EVERYTHING)
# ---------------------------------------------------------

def reset_log():
    """
    Hard reset: wipe all trades from the log file.
    Use when you want a clean slate.
    """
    try:
        with open(LOG_FILE, "w") as f:
            json.dump([], f)
        print("[TRADE_LOG] Log reset — all trades cleared", flush=True)
    except Exception as e:
        print(f"[TRADE_LOG] Failed to reset log: {e}", flush=True)