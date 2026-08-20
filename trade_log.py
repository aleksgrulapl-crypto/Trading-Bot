# ============================
# TRADE LOG MODULE (CLEAN + NORMALIZED SIDE)
# ============================

import json
import os
from datetime import datetime
import config

LOG_FILE = config.TRADE_LOG_FILE

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_raw_log():
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except:
        return []


def save_log(log):
    tmp = LOG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(log, f, indent=4)
    os.replace(tmp, LOG_FILE)


def normalize_side(side):
    if not side:
        return None
    s = side.upper()
    if s == "BUY":
        return "Long"
    if s == "SELL":
        return "Short"
    if s == "LONG":
        return "Long"
    if s == "SHORT":
        return "Short"
    return side.capitalize()


def log_open_trade(**kwargs):
    log = load_raw_log()

    entry = {
        "dealId": str(kwargs.get("deal_id")),
        "ticker": kwargs.get("ticker"),
        "epic": kwargs.get("epic"),
        "side": normalize_side(kwargs.get("side")),
        "size": float(kwargs.get("size")),
        "entry_price": float(kwargs.get("entry_price")),
        "exit_price": None,
        "pnl": None,
        "time_entered": kwargs.get("timestamp") or _now(),
        "time_exited": None,
        "status": "OPEN",
        "sl": kwargs.get("sl"),
        "tp": kwargs.get("tp"),
        "timeframe": kwargs.get("timeframe"),
    }

    log.append(entry)
    save_log(log)


def log_closed_trade(**kwargs):
    log = load_raw_log()

    entry = {
        "dealId": str(kwargs.get("deal_id")),
        "ticker": kwargs.get("ticker"),
        "epic": kwargs.get("epic"),
        "side": normalize_side(kwargs.get("side")),
        "size": float(kwargs.get("size")),
        "entry_price": float(kwargs.get("entry_price")),
        "exit_price": float(kwargs.get("exit_price")),
        "pnl": float(kwargs.get("pnl")),
        "time_entered": kwargs.get("time_entered"),
        "time_exited": kwargs.get("timestamp") or _now(),
        "status": "CLOSED",
        "sl": kwargs.get("sl"),
        "tp": kwargs.get("tp"),
        "timeframe": kwargs.get("timeframe"),
    }

    log.append(entry)
    save_log(log)
