# ============================
# TRADE LOG MODULE (MERGE ENGINE + PERSISTENT DISK)
# ============================

import json
import os
from datetime import datetime
import copy
import config

# Use the configured persistent path (e.g. /data/trade_log.json)
LOG_FILE = config.TRADE_LOG_FILE

# Ensure directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)


def load_raw_log():
    """
    Load the raw trade log (unmerged events).
    If the file does not exist, create an empty log and return [].
    """
    if not os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "w") as f:
                json.dump([], f)
        except Exception as e:
            print(f"[TRADE_LOG] Failed to initialize log file: {e}")
        return []
    try:
        with open(LOG_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        print(f"[TRADE_LOG] Failed to load log file: {e}")
        return []


def save_log(log):
    """
    Atomically save the log to disk.
    """
    tmp_file = LOG_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(log, f, indent=4)
    os.replace(tmp_file, LOG_FILE)


def _group_key(entry):
    """
    Group by logical trade identifier.
    """
    return (
        str(entry.get("trade_id") or entry.get("dealId") or entry.get("position_id")),
    )


def _is_open_event(entry):
    status = entry.get("status")
    side = (entry.get("side") or "").upper()
    exit_price = entry.get("exit_price")

    if status == "OPEN":
        return True
    if status in ("TRAIL_UPDATE",):
        return False
    if side in ("TRAIL", "CLOSE"):
        return False
    if exit_price is None:
        return True
    return False


def _is_close_event(entry):
    status = entry.get("status")
    side = (entry.get("side") or "").upper()
    exit_price = entry.get("exit_price")

    if status == "CLOSED":
        return True
    if side == "CLOSE":
        return True
    if exit_price is not None:
        return True
    return False


def _compute_pnl(open_event, close_event):
    try:
        size = float(close_event.get("size") or open_event.get("size") or 0)
        entry = float(open_event.get("entry_price") or close_event.get("entry_price") or 0)
        exit_ = float(close_event.get("exit_price") or 0)
    except Exception:
        return 0.0

    side = (open_event.get("side") or close_event.get("side") or "").upper()

    if side in ("BUY", "LONG"):
        return round((exit_ - entry) * size, 2)
    else:
        return round((entry - exit_) * size, 2)


def merge_trades(raw_log):
    """
    Merge multiple events per trade into a single logical trade:
    - OPEN only → stays OPEN
    - OPEN + CLOSED → becomes CLOSED with both prices and PnL
    - CLOSED only → stays CLOSED
    - TRAIL_UPDATE events are folded into the base trade
    """
    if not raw_log:
        return []

    groups = {}
    for entry in raw_log:
        key = _group_key(entry)
        groups.setdefault(key, []).append(entry)

    merged = []

    for key, entries in groups.items():
        open_event = None
        close_event = None
        trail_events = []

        for e in entries:
            if _is_open_event(e) and open_event is None:
                open_event = e
            if _is_close_event(e) and close_event is None:
                close_event = e
            if e.get("status") == "TRAIL_UPDATE":
                trail_events.append(e)

        if open_event and close_event:
            base = copy.deepcopy(open_event)

            base["status"] = "CLOSED"
            base["close_source"] = close_event.get("close_source") or base.get("close_source")
            base["reason"] = close_event.get("reason") or base.get("reason")
            base["notes"] = close_event.get("notes") or base.get("notes")
            base["fees"] = close_event.get("fees") if close_event.get("fees") is not None else base.get("fees", 0.0)

            base["exit_price"] = close_event.get("exit_price") or base.get("exit_price")
            base["close_timestamp"] = (
                close_event.get("close_timestamp")
                or close_event.get("time")
                or base.get("close_timestamp")
            )

            pnl = close_event.get("pnl")
            try:
                pnl = float(pnl)
            except (TypeError, ValueError):
                pnl = None

            if pnl is None:
                pnl = _compute_pnl(open_event, close_event)

            base["pnl"] = pnl

            if not base.get("open_timestamp"):
                base["open_timestamp"] = open_event.get("open_timestamp") or open_event.get("time")

            # Fold trail updates into base (latest SL)
            for t in trail_events:
                if t.get("sl") is not None:
                    base["sl"] = t.get("sl")

            merged.append(base)

        elif close_event and not open_event:
            base = copy.deepcopy(close_event)
            base["status"] = "CLOSED"
            if not base.get("open_timestamp"):
                base["open_timestamp"] = base.get("time")
            if not base.get("close_timestamp"):
                base["close_timestamp"] = base.get("time")

            pnl = base.get("pnl")
            try:
                pnl = float(pnl)
            except (TypeError, ValueError):
                pnl = 0.0
            base["pnl"] = pnl

            merged.append(base)

        elif open_event and not close_event:
            # Apply latest trail SL if any
            base = copy.deepcopy(open_event)
            for t in trail_events:
                if t.get("sl") is not None:
                    base["sl"] = t.get("sl")
            merged.append(base)

    return merged


def load_log():
    """
    Load merged log, sorted newest → oldest by close/open timestamp.
    """
    raw = load_raw_log()
    merged = merge_trades(raw)

    def _sort_key(x):
        return x.get("close_timestamp") or x.get("open_timestamp") or x.get("time")

    merged.sort(key=_sort_key, reverse=True)
    return merged


def log_trade(ticker, epic=None, deal_id=None, side=None, size=None,
              price=None, sl=None, tp=None, timestamp=None, timeframe=None):
    """
    Log an OPEN trade event.
    """
    log = load_raw_log()

    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = {
        "time": ts,
        "ticker": ticker,
        "epic": epic,
        "dealId": deal_id,
        "side": side.upper() if side else None,
        "size": float(size) if size is not None else 0.0,
        "entry_price": float(price) if price is not None else None,
        "exit_price": None,
        "pnl": None,
        "sl": sl,
        "tp": tp,
        "trail": None,
        "timeframe": timeframe,

        "trade_id": deal_id,
        "open_timestamp": ts,
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
    print(f"[TRADE_LOG] Logged OPEN trade → {ticker} {side} dealId={deal_id}")


def log_close(ticker, epic=None, deal_id=None, direction=None, size=None,
              entry_price=None, close_price=None, pnl=None,
              sl=None, tp=None, timestamp=None, timeframe=None):
    """
    Log a CLOSED trade event (used by close_position/history).
    """
    log = load_raw_log()

    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = {
        "time": ts,
        "ticker": ticker,
        "epic": epic,
        "dealId": deal_id,
        "side": direction.upper() if direction else "CLOSE",
        "size": float(size) if size is not None else 0.0,
        "entry_price": entry_price,
        "exit_price": float(close_price) if close_price is not None else None,
        "pnl": float(pnl) if pnl is not None else None,
        "sl": sl,
        "tp": tp,
        "trail": None,
        "timeframe": timeframe,

        "trade_id": deal_id,
        "open_timestamp": None,
        "close_timestamp": ts,
        "currency": "USD",
        "platform": "Capital",
        "reason": None,
        "checklist_passed": None,
        "close_source": "AUTO",
        "status": "CLOSED",
        "notes": None,
        "fees": 0.0,
        "cumulative_pnl": None,
        "running_peak": None,
        "drawdown": None
    }

    log.append(entry)
    save_log(log)
    print(f"[TRADE_LOG] Logged CLOSED trade → {ticker} dealId={deal_id}")


def log_trail_update(ticker, epic=None, deal_id=None, new_sl=None,
                     price=None, timestamp=None, timeframe=None):
    """
    Log a trailing stop update event.
    """
    log = load_raw_log()

    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    entry = {
        "time": ts,
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
    print(f"[TRADE_LOG] Logged TRAIL update → {ticker} dealId={deal_id}")
