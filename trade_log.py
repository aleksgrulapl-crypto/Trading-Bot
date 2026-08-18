# ============================
# TRADE LOG MODULE (STABLE OPEN/CLOSE FORMAT — NO MERGE)
# ============================

import json
import os

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
    Normalize a trade entry into a consistent format.
    Used for both OPEN and CLOSED trades.
    """

    side = entry.get("side")
    if side:
        side = str(side).upper()

    def to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "dealId": entry.get("dealId"),
        "ticker": entry.get("ticker"),
        "side": side,
        "size": to_float(entry.get("size")),

        "time_entered": entry.get("time_entered"),
        "time_exited": entry.get("time_exited"),

        "entry_price": to_float(entry.get("entry_price")),
        "exit_price": to_float(entry.get("exit_price")),

        "pnl": to_float(entry.get("pnl")) if entry.get("pnl") is not None else None,

        "status": entry.get("status") or (
            "OPEN" if entry.get("exit_price") in (None, "—") else "CLOSED"
        ),
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
        "status": "OPEN",
    }

    log.append(clean_trade(entry))
    save_log(log)

    print(f"[TRADE_LOG] Logged OPEN trade → {ticker} {side} dealId={deal_id}", flush=True)


# ---------------------------------------------------------
# LOG CLOSED TRADE
# ---------------------------------------------------------

def log_close(
    ticker,
    epic,
    deal_id,
    direction,
    size,
    entry_price,
    close_price,
    pnl,
    sl,
    tp,
    timestamp,
    timeframe,
):
    log = load_raw_log()

    # Try to find existing OPEN entry for this dealId
    found = False
    for t in log:
        if t.get("dealId") == deal_id:
            # Update existing entry in place
            t["exit_price"] = close_price
            t["pnl"] = pnl
            t["time_exited"] = timestamp
            t["status"] = "CLOSED"

            # Ensure entry_price is set
            if entry_price is not None:
                t["entry_price"] = entry_price

            # Ensure side/direction is consistent
            if direction:
                t["side"] = direction

            found = True
            break

    # If no existing entry (edge case), create a new CLOSED entry
    if not found:
        entry = {
            "dealId": deal_id,
            "ticker": ticker,
            "side": direction,
            "size": size,
            "entry_price": entry_price,
            "exit_price": close_price,
            "pnl": pnl,
            "time_entered": None,
            "time_exited": timestamp,
            "status": "CLOSED",
        }
        log.append(clean_trade(entry))

    save_log(log)

    print(
        f"[TRADE_LOG] Logged CLOSED trade → {ticker} {direction} pnl={pnl}",
        flush=True,
    )
