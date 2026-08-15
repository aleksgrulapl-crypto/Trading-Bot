# ============================
# TRADE LOG MODULE (UPGRADED — FULL TRADE LOG STRUCTURE + BACKWARD COMPATIBLE)
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

def log_trade(ticker, epic=None, deal_id=None, side=None, size=None,
              price=None, sl=None, tp=None, timestamp=None, timeframe=None):
    """
    Logs an OPEN trade.
    Existing fields preserved.
    New fields added for full Trade Log compatibility.
    """

    log = load_log()

    entry = {
        # Existing fields (unchanged)
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "epic": epic,
        "dealId": deal_id,
        "side": side.upper(),
        "size": float(size),
        "entry_price": float(price),
        "exit_price": None,
        "pnl": None,
        "sl": sl,
        "tp": tp,
        "trail": None,
        "timeframe": timeframe,

        # New fields (added safely)
        "trade_id": deal_id,                     # mirrors Capital.com
        "open_timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "close_timestamp": None,
        "currency": "USD",
        "platform": "Capital",
        "reason": None,
        "checklist_passed": None,
        "close_source": None,
        "status": "OPEN",
        "notes": None,
        "fees": 0.0,
        "cumulative_pnl": None,
        "running_peak": None,
        "drawdown": None
    }

    log.append(entry)
    save_log(log)


# ---------------------------------------------------------
# LOG CLOSED TRADE
# ---------------------------------------------------------

def log_close(ticker, epic=None, deal_id=None, direction=None, size=None,
              entry_price=None, close_price=None, pnl=None,
              sl=None, tp=None, timestamp=None, timeframe=None):
    """
    Logs a CLOSED trade with final PnL.
    Existing fields preserved.
    New fields added for full Trade Log compatibility.
    """

    log = load_log()

    entry = {
        # Existing fields
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "epic": epic,
        "dealId": deal_id,
        "side": direction.upper() if direction else "CLOSE",
        "size": float(size),
        "entry_price": entry_price,
        "exit_price": float(close_price),
        "pnl": float(pnl),
        "sl": sl,
        "tp": tp,
        "trail": None,
        "timeframe": timeframe,

        # New fields
        "trade_id": deal_id,
        "open_timestamp": None,                   # filled later by history.py
        "close_timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "currency": "USD",
        "platform": "Capital",
        "reason": None,
        "checklist_passed": None,
        "close_source": "AUTO",
        "status": "CLOSED",
        "notes": None,
        "fees": 0.0,

        # Performance fields (filled later)
        "cumulative_pnl": None,
        "running_peak": None,
        "drawdown": None
    }

    log.append(entry)
    save_log(log)


# ---------------------------------------------------------
# LOG TRAILING STOP UPDATE
# ---------------------------------------------------------

def log_trail_update(ticker, epic=None, deal_id=None, new_sl=None,
                     price=None, timestamp=None, timeframe=None):
    """
    Logs a trailing stop adjustment event.
    """

    log = load_log()

    entry = {
        # Existing fields
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "epic": epic,
        "dealId": deal_id,
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
        "timeframe": timeframe,

        # New fields
        "trade_id": deal_id,
        "open_timestamp": None,
        "close_timestamp": None,
        "currency": "USD",
        "platform": "Capital",
        "reason": "TRAIL UPDATE",
        "checklist_passed": None,
        "close_source": None,
        "status": "TRAIL_UPDATE",
        "notes": None,
        "fees": 0.0,
        "cumulative_pnl": None,
        "running_peak": None,
        "drawdown": None
    }

    log.append(entry)
    save_log(log)
