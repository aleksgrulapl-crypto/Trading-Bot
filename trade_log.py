# trade_log.py
# Robust file-backed trade log manager
# Stores trades as a list of dicts in JSON.

import json
import os
import tempfile
from datetime import datetime
import logging

logger = logging.getLogger("trade_log")
logging.basicConfig(level=logging.INFO)

LOG_PATH = os.environ.get("TRADE_LOG_PATH", "trade_log.json")
BACKUP_PATH = os.environ.get("TRADE_LOG_BACKUP", "trade_log.bak.json")
FLOAT_TOLERANCE = 1e-8


def _now_iso():
    return datetime.utcnow().isoformat() + "Z"


def _atomic_write(path, data):
    """Write JSON to path atomically using a temp file + replace."""
    dirn = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def load_raw_log():
    """Return list of trades (may be empty)."""
    if not os.path.exists(LOG_PATH):
        return []
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            logger.warning("trade_log: file content not a list, returning empty list")
            return []
    except Exception as e:
        logger.exception("trade_log: failed to load log: %s", e)
        return []


def save_raw_log(trades):
    """Atomically save trades list to disk (with backup)."""
    try:
        # backup current
        if os.path.exists(LOG_PATH):
            try:
                with open(LOG_PATH, "r", encoding="utf-8") as f:
                    old = f.read()
                with open(BACKUP_PATH, "w", encoding="utf-8") as bf:
                    bf.write(old)
            except Exception:
                logger.debug("trade_log: backup failed, continuing")

        ok = _atomic_write(LOG_PATH, trades)
        if not ok:
            logger.error("trade_log: atomic write failed")
            return False
        return True
    except Exception:
        logger.exception("trade_log: save failed")
        return False


def reset_log():
    """Clear the trade log safely."""
    save_raw_log([])
    return True


def append_open_trade(trade):
    """
    Append a new open trade.
    Expected trade keys: dealId, ticker, side, size, entry_price, time_entered
    """
    trades = load_raw_log()
    # normalize
    try:
        t = {
            "dealId": trade.get("dealId"),
            "ticker": trade.get("ticker"),
            "side": trade.get("side"),
            "size": float(trade.get("size", 0)),
            "entry_price": float(trade.get("entry_price", 0)),
            "time_entered": trade.get("time_entered") or _now_iso(),
            "exit_price": None,
            "time_exited": None,
            "pnl": None,
            "status": "OPEN",
            "notes": trade.get("notes")
        }
    except Exception:
        logger.exception("trade_log: invalid trade payload for append_open_trade")
        return None

    trades.append(t)
    save_raw_log(trades)
    return t


def _compute_pnl_for_trade(trade):
    """
    Compute PnL for a closed trade.
    For Long: (exit - entry) * size
    For Short: (entry - exit) * size
    """
    try:
        entry = float(trade.get("entry_price", 0))
        exitp = float(trade.get("exit_price", 0))
        size = float(trade.get("size", 0))
        side = (trade.get("side") or "").lower()
        if side == "long":
            pnl = (exitp - entry) * size
        else:
            pnl = (entry - exitp) * size
        return round(pnl, 2)
    except Exception:
        logger.exception("trade_log: failed to compute pnl")
        return None


def close_trade_by_dealId(dealId, exit_price=None, time_exited=None, note=None):
    """
    Mark trade closed by dealId. If exit_price is None, we still mark closed but pnl will be None.
    Returns the updated trade or None if not found.
    """
    trades = load_raw_log()
    updated = None
    for t in trades:
        if t.get("dealId") and str(t.get("dealId")) == str(dealId) and t.get("status") != "CLOSED":
            if exit_price is not None:
                try:
                    t["exit_price"] = float(exit_price)
                except Exception:
                    t["exit_price"] = exit_price
            t["time_exited"] = time_exited or _now_iso()
            t["status"] = "CLOSED"
            if t.get("exit_price") is not None:
                t["pnl"] = _compute_pnl_for_trade(t)
            else:
                t["pnl"] = None
            if note:
                t["notes"] = (t.get("notes") or "") + " | " + note
            updated = t
            break

    if updated:
        save_raw_log(trades)
    return updated


def _float_equal(a, b, tol=FLOAT_TOLERANCE):
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return str(a) == str(b)


def close_trade_fallback(ticker, entry_price, exit_price=None, time_exited=None, note=None):
    """
    If dealId is missing, try to find a matching open trade by ticker+entry_price and close it.
    Uses tolerant numeric comparison for entry_price.
    """
    trades = load_raw_log()
    updated = None
    for t in trades:
        if t.get("status") != "CLOSED" and t.get("ticker") == ticker:
            if _float_equal(t.get("entry_price", 0), entry_price):
                if exit_price is not None:
                    try:
                        t["exit_price"] = float(exit_price)
                    except Exception:
                        t["exit_price"] = exit_price
                t["time_exited"] = time_exited or _now_iso()
                t["status"] = "CLOSED"
                if t.get("exit_price") is not None:
                    t["pnl"] = _compute_pnl_for_trade(t)
                else:
                    t["pnl"] = None
                if note:
                    t["notes"] = (t.get("notes") or "") + " | " + note
                updated = t
                break

    if updated:
        save_raw_log(trades)
    return updated


def _make_signature(dealId, ticker, entry_price):
    return f"{dealId or ''}|{ticker or ''}|{round(float(entry_price or 0), 8)}"


def reconcile_with_positions(live_positions):
    """
    Reconcile local trade log with live positions.
    - If a trade in the log is OPEN but its dealId is present in the log and NOT present in live_positions -> mark CLOSED (time_exited now).
    - If a live position exists but not in the log -> append as OPEN.
    Returns dict: {"closed": [..], "added": [..]}
    """
    trades = load_raw_log()
    live_ids = set([p.get("dealId") for p in live_positions if p.get("dealId")])
    closed = []
    added = []

    # mark missing live ids as closed (only for trades that had a dealId)
    for t in trades:
        if t.get("status") != "CLOSED":
            did = t.get("dealId")
            if did and did not in live_ids:
                # mark closed (no exit price known)
                t["status"] = "CLOSED"
                t["time_exited"] = _now_iso()
                # pnl remains None unless exit_price is known
                t["pnl"] = _compute_pnl_for_trade(t) if t.get("exit_price") is not None else None
                closed.append(t)

    # build existing signatures for quick lookup
    existing_signatures = set()
    for t in trades:
        sig = _make_signature(t.get("dealId"), t.get("ticker"), t.get("entry_price"))
        existing_signatures.add(sig)

    # add live positions not in log
    for p in live_positions:
        # normalize fields from live position
        dealId = p.get("dealId")
        ticker = p.get("ticker") or p.get("market", {}).get("symbol") or p.get("instrument")
        # prefer explicit entry price fields; fall back to 'price' or 'level'
        entry_price = p.get("entry_price") or p.get("price") or p.get("level") or 0
        sig = _make_signature(dealId, ticker, entry_price)
        if sig not in existing_signatures:
            new = {
                "dealId": dealId,
                "ticker": ticker,
                "side": p.get("direction"),
                "size": float(p.get("size") or 0),
                "entry_price": float(entry_price or 0),
                "time_entered": p.get("time_entered") or _now_iso(),
                "exit_price": None,
                "time_exited": None,
                "pnl": None,
                "status": "OPEN",
                "notes": "Imported from live positions"
            }
            trades.append(new)
            added.append(new)
            existing_signatures.add(sig)

    if closed or added:
        save_raw_log(trades)

    return {"closed": closed, "added": added}


def get_completed_trades():
    trades = load_raw_log()
    return [t for t in trades if t.get("status") == "CLOSED"]


def get_open_trades():
    trades = load_raw_log()
    return [t for t in trades if t.get("status") != "CLOSED"]
