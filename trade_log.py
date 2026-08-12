# ============================
# TRADE LOG MODULE (FINAL CLEAN + TIMEFRAME SUPPORT)
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

def log_trade(ticker, side, size, price, timestamp=None, timeframe=None):
    """
    Logs an OPEN trade.
    pnl = None for open trades.
    timeframe = AutoTrader5M / AutoTrader15M / AutoTrader30M / AutoTrader1H
    """
    log = load_log()

    entry = {
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": side.upper(),          # BUY or SELL
        "size": float(size),
        "entry_price": float(price),   # renamed for clarity
        "exit_price": None,            # open trades have no exit yet
        "pnl": None,                   # open trades have no PnL yet
        "timeframe": timeframe         # NEW: strategy timeframe tag
    }

    log.append(entry)
    save_log(log)


# ---------------------------------------------------------
# LOG CLOSED TRADE
# ---------------------------------------------------------

def log_close(ticker, size, close_price, pnl, timestamp=None, timeframe=None):
    """
    Logs a CLOSED trade with final PnL.
    """
    log = load_log()

    entry = {
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": "CLOSE",
        "size": float(size),
        "entry_price": None,           # closed trades do not need entry price here
        "exit_price": float(close_price),
        "pnl": float(pnl),
        "timeframe": timeframe         # NEW: strategy timeframe tag
    }

    log.append(entry)
    save_log(log)
