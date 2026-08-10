import json
import os
from datetime import datetime
import config

LOG_FILE = config.TRADE_LOG_FILE

# Ensure directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def load_log():
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_log(log):
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=4)

def log_trade(ticker, side, size, price):
    log = load_log()
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": side,
        "size": size,
        "price": price,
        "pnl": None  # open trades have no PnL yet
    }
    log.append(entry)
    save_log(log)

def log_close(ticker, size, close_price, pnl):
    log = load_log()
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "side": "CLOSE",
        "size": size,
        "price": close_price,
        "pnl": pnl
    }
    log.append(entry)
    save_log(log)
