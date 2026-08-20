# trade_log.py
# Robust file-backed trade log manager
# Stores trades as a list of dicts in JSON.

import json
import os
import tempfile
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
import pytz

import config

logger = logging.getLogger("trade_log")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [trade_log] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

# Prefer config-provided path, fall back to env or default
LOG_PATH = getattr(config, "TRADE_LOG_PATH", None) or os.environ.get("TRADE_LOG_PATH") or os.environ.get("TRADE_LOG_FILE") or "trade_log.json"
BACKUP_PATH = os.environ.get("TRADE_LOG_BACKUP", "trade_log.bak.json")
FLOAT_TOLERANCE = 1e-8
UK_TZ = pytz.timezone("Europe/London")


def _now_iso() -> str:
    """
    Return current time in UK timezone as ISO string with timezone info.
    Example: 2026-08-20T18:54:08+01:00
    """
    now = datetime.now(UK_TZ)
    return now.isoformat()


def _atomic_write(path: str, data: Any) -> bool:
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


def load_raw_log() -> List[Dict[str, Any]]:
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


def save_raw_log(trades: List[Dict[str, Any]]) -> bool:
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


def reset_log() -> bool:
    """Clear the trade log safely."""
    return save_raw_log([])


def append_open_trade(trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Append a new open trade.
    Expected trade keys: dealId, dealReference, ticker, side, size, entry_price, time_entered
    Returns the appended trade dict or None on failure.
    """
    trades = load_raw_log()
    # normalize
    try:
        t = {
            "dealId": trade.get("dealId"),
            "dealReference": trade.get("dealReference"),
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
    if save_raw_log(trades):
        return t
    return None


def _compute_pnl_for_trade(trade: Dict[str, Any]) -> Optional[float]:
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


def close_trade_by_dealId(dealId: Any, exit_price: Any = None, time_exited: Optional[str] = None, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Mark trade closed by dealId. If exit_price is None, we still mark closed but pnl will be None.
    Returns the updated trade or None if not found.
    """
    trades = load_raw_log()
    updated = None
    for t in trades:
        if t.get("dealId") is not None and str(t.get("dealId")) == str(dealId) and t.get("status") != "CLOSED":
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


def _float_equal(a: Any, b: Any, tol: float = FLOAT_TOLERANCE) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return str(a) == str(b)


def close_trade_fallback(ticker: Any, entry_price: Any, exit_price: Any = None, time_exited: Optional[str] = None, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
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


def _make_signature(dealId: Any, dealReference: Any, ticker: Any, entry_price: Any) -> str:
    try:
        entry_norm = round(float(entry_price or 0), 8)
    except Exception:
        entry_norm = str(entry_price)
    return f"{dealId or ''}|{dealReference or ''}|{ticker or ''}|{entry_norm}"


def set_dealId_for_dealReference(dealReference: Any, dealId: Any) -> bool:
    """
    Update an existing log entry that was recorded with a dealReference but missing dealId.
    Returns True if updated.
    """
    if not dealReference or not dealId:
        return False

    trades = load_raw_log()
    updated = False
    for t in trades:
        if (t.get("dealReference") == dealReference) and (not t.get("dealId")):
            t["dealId"] = dealId
            # keep time_entered as-is; add note
            t["notes"] = (t.get("notes") or "") + f" | dealId_mapped={dealId}"
            updated = True
            break

    if updated:
        save_raw_log(trades)
    return updated


def reconcile_with_positions(live_positions: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Reconcile local trade log with live positions.
    - Match by dealId first, then dealReference, then fallback to ticker+entry_price.
    - If a trade in the log is OPEN but its dealId is present in the log and NOT present in live_positions -> mark CLOSED (time_exited now).
    - If a live position exists but not in the log -> append as OPEN.
    Returns dict: {"closed": [..], "added": [..]}
    """
    trades = load_raw_log()
    closed: List[Dict[str, Any]] = []
    added: List[Dict[str, Any]] = []

    # Build maps for quick lookup
    log_by_dealId = {}
    log_by_dealRef = {}
    for t in trades:
        if t.get("dealId") is not None:
            log_by_dealId[str(t.get("dealId"))] = t
        if t.get("dealReference"):
            log_by_dealRef[t.get("dealReference")] = t

    # Build set of live dealIds (as strings)
    live_ids = set()
    for p in live_positions or []:
        did = None
        if isinstance(p, dict):
            # support enriched and raw shapes
            did = p.get("dealId") or (p.get("position") or {}).get("dealId")
        if did is not None:
            live_ids.add(str(did))

    # mark missing live ids as closed (only for trades that had a dealId)
    for t in trades:
        if t.get("status") != "CLOSED":
            did = t.get("dealId")
            if did is not None and str(did) not in live_ids:
                # mark closed (no exit price known)
                t["status"] = "CLOSED"
                t["time_exited"] = _now_iso()
                # pnl remains None unless exit_price is known
                t["pnl"] = _compute_pnl_for_trade(t) if t.get("exit_price") is not None else None
                closed.append(t)

    # build existing signatures for quick lookup (includes dealReference)
    existing_signatures = set()
    for t in trades:
        sig = _make_signature(t.get("dealId"), t.get("dealReference"), t.get("ticker"), t.get("entry_price"))
        existing_signatures.add(sig)

    # add live positions not in log
    for p in live_positions or []:
        # normalize fields from live position
        dealId = None
        dealReference = None
        ticker = None
        entry_price = None
        side = None
        size = None
        time_entered = None
        exit_price = None
        time_exited = None

        if isinstance(p, dict):
            # enriched shape
            if p.get("dealId") is not None:
                dealId = p.get("dealId")
                dealReference = p.get("dealReference")
                ticker = p.get("ticker") or p.get("epic")
                entry_price = p.get("price") or p.get("entry_price") or p.get("level")
                side = p.get("side") or p.get("direction")
                size = p.get("size")
                time_entered = p.get("time_entered")
            else:
                # raw API shape
                pos = p.get("position") or {}
                market = p.get("market") or {}
                dealId = pos.get("dealId") or pos.get("dealReference")
                dealReference = pos.get("dealReference") or p.get("dealReference")
                ticker = market.get("symbol") or pos.get("instrumentName") or pos.get("instrument")
                entry_price = pos.get("level") or pos.get("price") or pos.get("entry_price")
                side = pos.get("direction")
                size = pos.get("size")
                time_entered = pos.get("time_entered") or pos.get("createdDate")

        sig = _make_signature(dealId, dealReference, ticker, entry_price)
        if sig not in existing_signatures:
            try:
                new = {
                    "dealId": dealId,
                    "dealReference": dealReference,
                    "ticker": ticker,
                    "side": side,
                    "size": float(size or 0),
                    "entry_price": float(entry_price or 0),
                    "time_entered": time_entered or _now_iso(),
                    "exit_price": None,
                    "time_exited": None,
                    "pnl": None,
                    "status": "OPEN",
                    "notes": "Imported from live positions"
                }
            except Exception:
                # fallback to safer defaults
                new = {
                    "dealId": dealId,
                    "dealReference": dealReference,
                    "ticker": ticker,
                    "side": side,
                    "size": size or 0,
                    "entry_price": entry_price or 0,
                    "time_entered": time_entered or _now_iso(),
                    "exit_price": None,
                    "time_exited": None,
                    "pnl": None,
                    "status": "OPEN",
                    "notes": "Imported from live positions (partial)"
                }
            trades.append(new)
            added.append(new)
            existing_signatures.add(sig)

    if closed or added:
        save_raw_log(trades)

    return {"closed": closed, "added": added}


def get_completed_trades() -> List[Dict[str, Any]]:
    trades = load_raw_log()
    return [t for t in trades if t.get("status") == "CLOSED"]


def get_open_trades() -> List[Dict[str, Any]]:
    trades = load_raw_log()
    return [t for t in trades if t.get("status") != "CLOSED"]


# -------------------------
# Backwards compatibility wrappers
# -------------------------
# Many older modules import legacy names like log_open_trade / log_closed_trade / get_trades.
# Provide thin aliases that map to the canonical functions above.

def log_open_trade(*args, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Backwards-compatible alias for append_open_trade.
    Accepts either a single dict positional arg or keyword args.
    """
    if args and isinstance(args[0], dict):
        return append_open_trade(args[0])
    # build dict from kwargs
    payload = {
        "dealId": kwargs.get("dealId") or kwargs.get("deal_id") or kwargs.get("dealid"),
        "dealReference": kwargs.get("dealReference") or kwargs.get("deal_reference"),
        "ticker": kwargs.get("ticker") or kwargs.get("epic") or kwargs.get("symbol"),
        "side": kwargs.get("side") or kwargs.get("direction"),
        "size": kwargs.get("size") or kwargs.get("qty") or kwargs.get("quantity"),
        "entry_price": kwargs.get("entry_price") or kwargs.get("entryPrice") or kwargs.get("price"),
        "time_entered": kwargs.get("time_entered") or kwargs.get("timestamp") or kwargs.get("time"),
        "notes": kwargs.get("notes")
    }
    return append_open_trade(payload)


def log_closed_trade(*args, **kwargs) -> Optional[Dict[str, Any]]:
    """
    Backwards-compatible alias for close_trade_by_dealId or close_trade_fallback.
    Usage patterns supported:
      - log_closed_trade(dealId, exit_price=..., time_exited=..., note=...)
      - log_closed_trade(ticker=..., entry_price=..., exit_price=..., ...)
    """
    # If first positional arg is a dict, try to extract fields
    if args and isinstance(args[0], dict):
        d = args[0]
        dealId = d.get("dealId") or d.get("deal_id")
        exit_price = d.get("exit_price") or d.get("exitPrice") or d.get("price")
        time_exited = d.get("time_exited") or d.get("timeExited") or d.get("time")
        note = d.get("note") or d.get("notes")
        if dealId:
            return close_trade_by_dealId(dealId, exit_price=exit_price, time_exited=time_exited, note=note)
        # fallback to ticker+entry_price
        ticker = d.get("ticker")
        entry_price = d.get("entry_price") or d.get("entryPrice") or d.get("price")
        if ticker and entry_price is not None:
            return close_trade_fallback(ticker, entry_price, exit_price=exit_price, time_exited=time_exited, note=note)
        return None

    # positional style: (dealId, exit_price?)
    if args and len(args) >= 1:
        dealId = args[0]
        exit_price = args[1] if len(args) >= 2 else kwargs.get("exit_price") or kwargs.get("exitPrice")
        time_exited = kwargs.get("time_exited") or kwargs.get("timeExited")
        note = kwargs.get("note")
        return close_trade_by_dealId(dealId, exit_price=exit_price, time_exited=time_exited, note=note)

    # keyword style for fallback
    if "dealId" in kwargs or "deal_id" in kwargs:
        dealId = kwargs.get("dealId") or kwargs.get("deal_id")
        return close_trade_by_dealId(dealId, exit_price=kwargs.get("exit_price"), time_exited=kwargs.get("time_exited"), note=kwargs.get("note"))

    if "ticker" in kwargs and "entry_price" in kwargs:
        return close_trade_fallback(kwargs.get("ticker"), kwargs.get("entry_price"), exit_price=kwargs.get("exit_price"), time_exited=kwargs.get("time_exited"), note=kwargs.get("note"))

    return None


def get_trades() -> List[Dict[str, Any]]:
    """Backwards-compatible alias for load_raw_log."""
    return load_raw_log()
