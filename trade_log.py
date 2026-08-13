# ============================
# TRADE LOG MODULE (FINAL VERSION — SL/TP + TRAIL SUPPORT)
# ============================

import json
import os
from datetime import datetime
import config

LOG_FILE = config.TRADE_LOG_FILE

# Ensure directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# ---------------------------------------------------------
# LOAD LOG
# ---------------------------------------------------------

def load_log():
    """
    Loads the trade log safely.
    Returns an empty list if file missing or corrupted.
    """
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except:
        return []


# ---------------------------------------------------------
# SAVE LOG (atomic write)
# ---------------------------------------------------------

def save_log(log):
    """
    Saves the trade log using atomic write to avoid corruption.
    """
    tmp_file = LOG_FILE + ".tmp"

    with open(tmp_file, "w") as f:
        json.dump(log, f, indent=4)

    os.replace(tmp_file, LOG_FILE)


# ---------------------------------------------------------
# LOG OPEN TRADE
# ---------------------------------------------------------

def log_trade(ticker, side, size, price, sl=None, tp=None, timestamp=None, timeframe=None):
    """
    Logs an OPEN trade.
    Includes SL/TP and timeframe.
    """
    log = load_log()

    entry = {
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": side.upper(),          # BUY or SELL
        "size": float(size),
        "entry_price": float(price),
        "exit_price": None,
        "pnl": None,
        "sl": sl,
        "tp": tp,
        "trail": None,                 # future trailing stop updates
        "timeframe": timeframe
    }

    log.append(entry)
    save_log(log)


# ---------------------------------------------------------
# LOG CLOSED TRADE
# ---------------------------------------------------------

def log_close(ticker, size, close_price, pnl, sl=None, tp=None, timestamp=None, timeframe=None):
    """
    Logs a CLOSED trade with final PnL.
    Includes SL/TP and timeframe.
    """
    log = load_log()

    entry = {
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": "CLOSE",
        "size": float(size),
        "entry_price": None,
        "exit_price": float(close_price),
        "pnl": float(pnl),
        "sl": sl,
        "tp": tp,
        "trail": None,
        "timeframe": timeframe
    }

    log.append(entry)
    save_log(log)


# ---------------------------------------------------------
# LOG TRAILING STOP UPDATE
# ---------------------------------------------------------

def log_trail_update(ticker, new_sl, price, timestamp=None, timeframe=None):
    """
    Logs a trailing stop adjustment event.
    """
    log = load_log()

    entry = {
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": "TRAIL",
        "size": None,
        "entry_price": None,
        "exit_price": None,
        "pnl": None,
        "sl": new_sl,
        "tp": None,
        "trail": {
            "new_sl": new_sl,
            "price": price
        },
        "timeframe": timeframe
    }

    log.append(entry)
    save_log(log)
