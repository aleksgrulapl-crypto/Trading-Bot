#!/usr/bin/env python3
# trade_log.py
# Robust file-backed trade log manager with:
# - human-readable timestamps (time_entered_human, time_exited_human)
# - USD->GBP pnl conversion (pnl_gbp) via FX_USD_GBP env var
# - safe atomic writes and backups
# - backwards-compatible API used by the bot

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

# Optional dependency: pytz if available for UK timezone handling
try:
    import pytz
    UK_TZ = pytz.timezone("Europe/London")
except Exception:
    UK_TZ = None

logger = logging.getLogger("trade_log")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [trade_log] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

# Configurable paths and FX
LOG_PATH = os.environ.get("TRADE_LOG_PATH") or os.environ.get("TRADE_LOG_FILE") or "/data/trade_log.json"
BACKUP_PATH = os.environ.get("TRADE_LOG_BACKUP") or (os.path.splitext(LOG_PATH)[0] + ".bak.json")
FX_USD_GBP = float(os.environ.get("FX_USD_GBP", "0.78"))  # override in env if needed
FLOAT_TOLERANCE = 1e-8

def _now_iso() -> str:
    """Return current time in UK timezone as ISO string with timezone info if possible."""
    now = datetime.utcnow()
    if UK_TZ:
        try:
            return datetime.now(UK_TZ).isoformat()
        except Exception:
            pass
    return now.isoformat() + "Z"

def _atomic_write(path: str, data: Any) -> bool:
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

def _parse_iso_like(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # Accept ISO with offset
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        # Try common formats
        for fmt in ("%Y-%m-%d %H.%M.%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return None

def _humanize(dt_str: Optional[str]) -> Optional[str]:
    d = _parse_iso_like(dt_str)
    if not d:
        return None
    return d.strftime("%d-%m-%Y %H:%M:%S")

def load_raw_log(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                logger.warning("trade_log: file content not a list, returning empty list")
                return []
            # augment with human fields and pnl_gbp
            for e in data:
                e["time_entered_human"] = _humanize(e.get("time_entered"))
                e["time_exited_human"] = _humanize(e.get("time_exited"))
                try:
                    if e.get("pnl") not in (None, ""):
                        e["pnl_gbp"] = round(float(e.get("pnl")) * FX_USD_GBP, 2)
                    else:
                        e["pnl_gbp"] = None
                except Exception:
                    e["pnl_gbp"] = None
            return data
    except Exception as exc:
        logger.exception("trade_log: failed to load log: %s", exc)
        return []

def save_raw_log(trades: List[Dict[str, Any]], path: str = LOG_PATH) -> bool:
    try:
        # attempt to backup existing file
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    old = f.read()
                with open(BACKUP_PATH, "w", encoding="utf-8") as bf:
                    bf.write(old)
        except Exception:
            logger.debug("trade_log: backup failed, continuing")

        ok = _atomic_write(path, trades)
        if not ok:
            logger.error("trade_log: atomic write failed")
            return False
        return True
    except Exception:
        logger.exception("trade_log: save failed")
        return False

def reset_log(path: str = LOG_PATH) -> bool:
    return save_raw_log([], path)

def _compute_pnl_for_trade(trade: Dict[str, Any]) -> Optional[float]:
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

def append_open_trade(trade: Dict[str, Any], path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    trades = load_raw_log(path)
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
            "pnl_gbp": None,
            "status": "OPEN",
            "notes": trade.get("notes")
        }
    except Exception:
        logger.exception("trade_log: invalid trade payload for append_open_trade")
        return None

    # add human fields
    t["time_entered_human"] = _humanize(t.get("time_entered"))
    t["time_exited_human"] = None

    trades.append(t)
    if save_raw_log(trades, path):
        return t
    return None

def close_trade_by_dealId(dealId: Any, exit_price: Any = None, time_exited: Optional[str] = None, note: Optional[str] = None, path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    trades = load_raw_log(path)
    updated = None
    for t in trades:
        if t.get("dealId") is not None and str(t.get("dealId")) == str(dealId) and t.get("status") != "CLOSED":
            if exit_price is not None:
                try:
                    t["exit_price"] = float(exit_price)
                except Exception:
                    t["exit_price"] = exit_price
            t["time_exited"] = time_exited or _now_iso()
            t["time_exited_human"] = _humanize(t.get("time_exited"))
            t["status"] = "CLOSED"
            if t.get("exit_price") is not None:
                t["pnl"] = _compute_pnl_for_trade(t)
                try:
                    t["pnl_gbp"] = round(float(t["pnl"]) * FX_USD_GBP, 2) if t.get("pnl") not in (None, "") else None
                except Exception:
                    t["pnl_gbp"] = None
            else:
                t["pnl"] = None
                t["pnl_gbp"] = None
            if note:
                t["notes"] = (t.get("notes") or "") + " | " + note
            updated = t
            break

    if updated:
        save_raw_log(trades, path)
    return updated

def _float_equal(a: Any, b: Any, tol: float = FLOAT_TOLERANCE) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return str(a) == str(b)

def close_trade_fallback(ticker: Any, entry_price: Any, exit_price: Any = None, time_exited: Optional[str] = None, note: Optional[str] = None, path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    trades = load_raw_log(path)
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
                t["time_exited_human"] = _humanize(t.get("time_exited"))
                t["status"] = "CLOSED"
                if t.get("exit_price") is not None:
                    t["pnl"] = _compute_pnl_for_trade(t)
                    try:
                        t["pnl_gbp"] = round(float(t["pnl"]) * FX_USD_GBP, 2) if t.get("pnl") not in (None, "") else None
                    except Exception:
                        t["pnl_gbp"] = None
                else:
                    t["pnl"] = None
                    t["pnl_gbp"] = None
                if note:
                    t["notes"] = (t.get("notes") or "") + " | " + note
                updated = t
                break

    if updated:
        save_raw_log(trades, path)
    return updated

def _make_signature(dealId: Any, dealReference: Any, ticker: Any, entry_price: Any) -> str:
    try:
        entry_norm = round(float(entry_price or 0), 8)
    except Exception:
        entry_norm = str(entry_price)
    return f"{dealId or ''}|{dealReference or ''}|{ticker or ''}|{entry_norm}"

def set_dealId_for_dealReference(dealReference: Any, dealId: Any, path: str = LOG_PATH) -> bool:
    if not dealReference or not dealId:
        return False
    trades = load_raw_log(path)
    updated = False
    for t in trades:
        if (t.get("dealReference") == dealReference) and (not t.get("dealId")):
            t["dealId"] = dealId
            t["notes"] = (t.get("notes") or "") + f" | dealId_mapped={dealId}"
            updated = True
            break
    if updated:
        save_raw_log(trades, path)
    return updated

def reconcile_with_positions(live_positions: List[Dict[str, Any]], path: str = LOG_PATH) -> Dict[str, List[Dict[str, Any]]]:
    trades = load_raw_log(path)
    closed: List[Dict[str, Any]] = []
    added: List[Dict[str, Any]] = []

    # Build maps
    log_by_dealId = {}
    log_by_dealRef = {}
    for t in trades:
        if t.get("dealId") is not None:
            log_by_dealId[str(t.get("dealId"))] = t
        if t.get("dealReference"):
            log_by_dealRef[t.get("dealReference")] = t

    # live ids
    live_ids = set()
    for p in live_positions or []:
        did = None
        if isinstance(p, dict):
            did = p.get("dealId") or (p.get("position") or {}).get("dealId")
        if did is not None:
            live_ids.add(str(did))

    # mark missing live ids as closed
    for t in trades:
        if t.get("status") != "CLOSED":
            did = t.get("dealId")
            if did is not None and str(did) not in live_ids:
                exit_price = None
                time_exited = None
                for p in live_positions or []:
                    pdid = p.get("dealId") or (p.get("position") or {}).get("dealId")
                    if pdid and str(pdid) == str(did):
                        exit_price = p.get("exit_price") or p.get("closePrice") or p.get("closedPrice") or p.get("price")
                        time_exited = p.get("time_exited") or p.get("timeExited") or p.get("closedDate")
                        break
                if exit_price is not None:
                    try:
                        t["exit_price"] = float(exit_price)
                    except Exception:
                        t["exit_price"] = exit_price
                t["time_exited"] = time_exited or _now_iso()
                t["time_exited_human"] = _humanize(t.get("time_exited"))
                t["status"] = "CLOSED"
                t["pnl"] = _compute_pnl_for_trade(t) if t.get("exit_price") is not None else None
                try:
                    t["pnl_gbp"] = round(float(t["pnl"]) * FX_USD_GBP, 2) if t.get("pnl") not in (None, "") else None
                except Exception:
                    t["pnl_gbp"] = None
                closed.append(t)

    # existing signatures
    existing_signatures = set()
    for t in trades:
        sig = _make_signature(t.get("dealId"), t.get("dealReference"), t.get("ticker"), t.get("entry_price"))
        existing_signatures.add(sig)

    # add live positions not in log
    for p in live_positions or []:
        dealId = None
        dealReference = None
        ticker = None
        entry_price = None
        side = None
        size = None
        time_entered = None

        if isinstance(p, dict):
            if p.get("dealId") is not None:
                dealId = p.get("dealId")
                dealReference = p.get("dealReference")
                ticker = p.get("ticker") or p.get("epic")
                entry_price = p.get("price") or p.get("entry_price") or p.get("level")
                side = p.get("side") or p.get("direction")
                size = p.get("size")
                time_entered = p.get("time_entered") or p.get("createdDate")
            else:
                pos = p.get("position") or {}
                market = p.get("market") or {}
                dealId = pos.get("dealId") or pos.get("dealReference")
                dealReference = pos.get("dealReference") or p.get("dealReference")
                ticker = market.get("symbol") or pos.get("instrumentName") or pos.get("instrument")
                entry_price = pos.get("level") or pos.get("price") or pos.get("entry_price")
                side = pos.get("direction")
                size = pos.get("size")
                time_entered = pos.get("createdDate") or pos.get("time_entered")

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
                    "time_exited": None,
                    "pnl": None,
                    "pnl_gbp": None,
                    "status": "OPEN",
                    "notes": "Imported from live positions"
                }
            except Exception:
                new = {
                    "dealId": dealId,
                    "dealReference": dealReference,
                    "ticker": ticker,
                    "side": side,
                    "size": size or 0,
                    "entry_price": entry_price or 0,
                    "time_entered": time_entered or _now_iso(),
                    "time_exited": None,
                    "pnl": None,
                    "pnl_gbp": None,
                    "status": "OPEN",
                    "notes": "Imported from live positions (partial)"
                }
            new["time_entered_human"] = _humanize(new.get("time_entered"))
            trades.append(new)
            added.append(new)
            existing_signatures.add(sig)

    if closed or added:
        save_raw_log(trades, path)

    return {"closed": closed, "added": added}

def get_completed_trades(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    trades = load_raw_log(path)
    return [t for t in trades if t.get("status") == "CLOSED"]

def get_open_trades(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    trades = load_raw_log(path)
    return [t for t in trades if t.get("status") != "CLOSED"]

# Backwards compatibility wrappers
def log_open_trade(*args, **kwargs) -> Optional[Dict[str, Any]]:
    if args and isinstance(args[0], dict):
        return append_open_trade(args[0])
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
    if args and isinstance(args[0], dict):
        d = args[0]
        dealId = d.get("dealId") or d.get("deal_id")
        exit_price = d.get("exit_price") or d.get("exitPrice") or d.get("price")
        time_exited = d.get("time_exited") or d.get("timeExited") or d.get("time")
        note = d.get("note") or d.get("notes")
        if dealId:
            return close_trade_by_dealId(dealId, exit_price=exit_price, time_exited=time_exited, note=note)
        ticker = d.get("ticker")
        entry_price = d.get("entry_price") or d.get("entryPrice") or d.get("price")
        if ticker and entry_price is not None:
            return close_trade_fallback(ticker, entry_price, exit_price=exit_price, time_exited=time_exited, note=note)
        return None

    if args and len(args) >= 1:
        dealId = args[0]
        exit_price = args[1] if len(args) >= 2 else kwargs.get("exit_price") or kwargs.get("exitPrice")
        time_exited = kwargs.get("time_exited") or kwargs.get("timeExited")
        note = kwargs.get("note")
        return close_trade_by_dealId(dealId, exit_price=exit_price, time_exited=time_exited, note=note)

    if "dealId" in kwargs or "deal_id" in kwargs:
        dealId = kwargs.get("dealId") or kwargs.get("deal_id")
        return close_trade_by_dealId(dealId, exit_price=kwargs.get("exit_price"), time_exited=kwargs.get("time_exited"), note=kwargs.get("note"))

    if "ticker" in kwargs and "entry_price" in kwargs:
        return close_trade_fallback(kwargs.get("ticker"), kwargs.get("entry_price"), exit_price=kwargs.get("exit_price"), time_exited=kwargs.get("time_exited"), note=kwargs.get("note"))

    return None

def get_trades(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    return load_raw_log(path)
