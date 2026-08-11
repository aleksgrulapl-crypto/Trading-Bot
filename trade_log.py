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
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except:
        return []

# ---------------------------------------------------------
# SAVE LOG
# ---------------------------------------------------------

def save_log(log):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=4)

# ---------------------------------------------------------
# LOG OPEN TRADE
# ---------------------------------------------------------

def log_trade(ticker, side, size, price, pnl=None, timestamp=None):
    log = load_log()

    entry = {
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": side,
        "size": size,
        "price": price,
        "pnl": pnl  # None for open trades
    }

    log.append(entry)
    save_log(log)

# ---------------------------------------------------------
# LOG CLOSED TRADE
# ---------------------------------------------------------

def log_close(ticker, size, close_price, pnl, timestamp=None):
    log = load_log()

    entry = {
        "time": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": "CLOSE",
        "size": size,
        "price": close_price,
        "pnl": pnl
    }

    log.append(entry)
    save_log(log)
