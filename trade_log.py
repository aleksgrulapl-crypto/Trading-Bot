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


# ---------------------------------------------------------
# TIME HELPER
# ---------------------------------------------------------

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------
# RAW LOAD
# ---------------------------------------------------------

def load_raw_log():
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[TRADE_LOG] Failed to load log: {e}")
        return []


# ---------------------------------------------------------
# SAFE SAVE
# ---------------------------------------------------------

def save_log(log):
    tmp_file = LOG_FILE + ".tmp"
    try:
        with open(tmp_file, "w") as f:
            json.dump(log, f, indent=4)
        os.replace(tmp_file, LOG_FILE)
    except Exception as e:
        print(f"[TRADE_LOG] Failed to save log: {e}")


# ---------------------------------------------------------
# NORMALIZE SIDE (GLOBAL CONSISTENCY)
# ---------------------------------------------------------

def _normalize_side(side):
    """
    Normalize BUY/SELL/LONG/SHORT into:
        Long
        Short
    """
    if not side:
        return None

    s = str(side).upper()

    if s == "BUY":
        return "Long"
    if s == "SELL":
        return "Short"
    if s == "LONG":
        return "Long"
    if s == "SHORT":
        return "Short"

    # fallback: capitalize first letter
    return s.capitalize()


# ---------------------------------------------------------
# LOG OPEN TRADE
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
    log = load_raw_log()

    norm_side = _normalize_side(side)

    entry = {
        "dealId": str(deal_id) if deal_id else None,
        "ticker": ticker or epic,
        "epic": epic,
        "side": norm_side,                     # ALWAYS Long / Short
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
        f"[TRADE_LOG] Logged OPEN trade → {ticker} {norm_side} size={size} dealId={deal_id}",
        flush=True,
    )


# ---------------------------------------------------------
# LOG CLOSED TRADE
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
    log = load_raw_log()

    norm_side = _normalize_side(side)

    entry = {
        "dealId": str(deal_id) if deal_id else None,
        "ticker": ticker or epic,
        "epic": epic,
        "side": norm_side,                     # ALWAYS Long / Short
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
        f"[TRADE_LOG] Logged CLOSED trade → {ticker} {norm_side} size={size} pnl={pnl}",
        flush=True,
    )


# ---------------------------------------------------------
# RESET LOG
# ---------------------------------------------------------

def reset_log():
    try:
        with open(LOG_FILE, "w") as f:
            json.dump([], f)
        print("[TRADE_LOG] Log reset — all trades cleared", flush=True)
    except Exception as e:
        print(f"[TRADE_LOG] Failed to reset log: {e}", flush=True)
