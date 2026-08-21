#!/usr/bin/env python3
# trade_log.py
# Unified, safe trade log manager with environment FX sync (option B)
# - canonical upsert for open trades
# - centralized close logic computing pnl and pnl_gbp (reads FX from env at compute time)
# - preserves ISO timestamps internally; adds human fields for display
# - rejects malformed appends instead of defaulting entry_price to 0
# - syncs config.FX_USD_GBP into environment at import time if present

import json
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

# Try to import config and, if present, ensure FX_USD_GBP is available in the environment.
try:
    import config
    # If config defines FX_USD_GBP, ensure the environment variable is set so other modules reading env see it.
    try:
        if getattr(config, "FX_USD_GBP", None) is not None:
            os.environ.setdefault("FX_USD_GBP", str(config.FX_USD_GBP))
    except Exception:
        # ignore any config-related issues; we'll fall back to env/defaults below
        pass
except Exception:
    config = None

# Optional timezone support
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

LOG_PATH = os.environ.get("TRADE_LOG_PATH") or os.environ.get("TRADE_LOG_FILE") or "/data/trade_log.json"
BACKUP_PATH = os.environ.get("TRADE_LOG_BACKUP") or (os.path.splitext(LOG_PATH)[0] + ".bak.json")
FLOAT_TOLERANCE = 1e-8

def _now_iso() -> str:
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
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
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

def _read_fx_rate() -> float:
    """
    Read FX rate from environment. This function is used at compute time so
    changes to the environment (or config sync at startup) are respected.
    """
    try:
        return float(os.environ.get("FX_USD_GBP", "0.78"))
    except Exception:
        return 0.78

def load_raw_log(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                logger.warning("trade_log: file content not a list, returning empty list")
                return []
            fx = _read_fx_rate()
            for e in data:
                e["time_entered_human"] = _humanize(e.get("time_entered"))
                e["time_exited_human"] = _humanize(e.get("time_exited"))
                try:
                    if e.get("pnl") not in (None, ""):
                        e["pnl_gbp"] = round(float(e.get("pnl")) * fx, 2)
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

def _float_equal(a: Any, b: Any, tol: float = FLOAT_TOLERANCE) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return str(a) == str(b)

def _compute_pnl_for_trade(trade: Dict[str, Any]) -> Optional[float]:
    try:
        entry = float(trade.get("entry_price"))
        exitp = float(trade.get("exit_price"))
        size = float(trade.get("size", 0))
        side = (trade.get("side") or "").lower()
        if side in ("long", "buy"):
            pnl = (exitp - entry) * size
        else:
            pnl = (entry - exitp) * size
        return round(pnl, 2)
    except Exception:
        logger.exception("trade_log: failed to compute pnl")
        return None

def _make_signature(dealId: Any, dealReference: Any, ticker: Any, entry_price: Any) -> str:
    try:
        entry_norm = round(float(entry_price or 0), 8)
    except Exception:
        entry_norm = str(entry_price)
    return f"{dealId or ''}|{dealReference or ''}|{ticker or ''}|{entry_norm}"

def find_trade(dealId: Optional[str]=None, dealReference: Optional[str]=None, ticker: Optional[str]=None, entry_price: Optional[float]=None, path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    trades = load_raw_log(path)
    if dealId:
        for t in trades:
            if t.get("dealId") and str(t.get("dealId")) == str(dealId):
                return t
    if dealReference:
        for t in trades:
            if t.get("dealReference") and str(t.get("dealReference")) == str(dealReference):
                return t
    sig = _make_signature(dealId, dealReference, ticker, entry_price)
    for t in trades:
        if _make_signature(t.get("dealId"), t.get("dealReference"), t.get("ticker"), t.get("entry_price")) == sig:
            return t
    return None

def upsert_open_trade(payload: Dict[str, Any], path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    pos = payload.get("position") or payload.get("raw") or payload
    market = payload.get("market") or (pos.get("market") if isinstance(pos, dict) else None)

    dealId = payload.get("dealId") or (pos.get("dealId") if isinstance(pos, dict) else None)
    dealReference = payload.get("dealReference") or (pos.get("dealReference") if isinstance(pos, dict) else None)
    ticker = payload.get("ticker") or (market.get("symbol") if isinstance(market, dict) else None) or (pos.get("instrument") if isinstance(pos, dict) else None)
    side = payload.get("side") or (pos.get("direction") if isinstance(pos, dict) else None)
    size = payload.get("size") or (pos.get("size") if isinstance(pos, dict) else None) or (pos.get("contractSize") if isinstance(pos, dict) else None)
    entry_price = payload.get("entry_price") or (pos.get("level") if isinstance(pos, dict) else None) or (pos.get("entryPrice") if isinstance(pos, dict) else None)
    time_entered = payload.get("time_entered") or (pos.get("createdDate") if isinstance(pos, dict) else None) or (pos.get("createdDateUTC") if isinstance(pos, dict) else None)

    try:
        size_val = float(size) if size not in (None, "") else None
    except Exception:
        size_val = None
    try:
        entry_val = float(entry_price) if entry_price not in (None, "") else None
    except Exception:
        entry_val = None

    if entry_val is None or size_val is None:
        logger.warning("upsert_open_trade: missing size or entry_price; rejecting payload: dealId=%s dealRef=%s ticker=%s", dealId, dealReference, ticker)
        return None

    trades = load_raw_log(path)
    existing = None
    if dealId:
        for t in trades:
            if t.get("dealId") and str(t.get("dealId")) == str(dealId):
                existing = t
                break
    if not existing and dealReference:
        for t in trades:
            if t.get("dealReference") and str(t.get("dealReference")) == str(dealReference):
                existing = t
                break
    if not existing:
        sig = _make_signature(dealId, dealReference, ticker, entry_val)
        for t in trades:
            if _make_signature(t.get("dealId"), t.get("dealReference"), t.get("ticker"), t.get("entry_price")) == sig:
                existing = t
                break

    if existing:
        updated = False
        if not existing.get("dealId") and dealId:
            existing["dealId"] = dealId; updated = True
        if not existing.get("dealReference") and dealReference:
            existing["dealReference"] = dealReference; updated = True
        if not existing.get("ticker") and ticker:
            existing["ticker"] = ticker; updated = True
        if not existing.get("side") and side:
            existing["side"] = side; updated = True
        if (existing.get("size") in (None, 0)) and size_val is not None:
            existing["size"] = size_val; updated = True
        if (existing.get("entry_price") in (None, "")) and entry_val is not None:
            existing["entry_price"] = entry_val; updated = True
        if (existing.get("time_entered") in (None, "")) and time_entered:
            existing["time_entered"] = time_entered; updated = True
        if updated:
            existing["time_entered_human"] = _humanize(existing.get("time_entered"))
            save_raw_log(trades, path)
        return existing

    new = {
        "dealId": dealId,
        "dealReference": dealReference,
        "ticker": ticker,
        "side": side,
        "size": size_val,
        "entry_price": entry_val,
        "time_entered": time_entered or _now_iso(),
        "time_entered_human": _humanize(time_entered or _now_iso()),
        "exit_price": None,
        "time_exited": None,
        "time_exited_human": None,
        "pnl": None,
        "pnl_gbp": None,
        "status": "OPEN",
        "notes": payload.get("notes") or "Imported"
    }
    trades.append(new)
    if save_raw_log(trades, path):
        return new
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
                    fx = _read_fx_rate()
                    t["pnl_gbp"] = round(float(t["pnl"]) * fx, 2) if t.get("pnl") not in (None, "") else None
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
                        fx = _read_fx_rate()
                        t["pnl_gbp"] = round(float(t["pnl"]) * fx, 2) if t.get("pnl") not in (None, "") else None
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

    live_ids = set()
    for p in live_positions or []:
        did = None
        if isinstance(p, dict):
            did = p.get("dealId") or (p.get("position") or {}).get("dealId")
        if did is not None:
            live_ids.add(str(did))

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
                    fx = _read_fx_rate()
                    t["pnl_gbp"] = round(float(t["pnl"]) * fx, 2) if t.get("pnl") not in (None, "") else None
                except Exception:
                    t["pnl_gbp"] = None
                closed.append(t)

    existing_signatures = set()
    for t in trades:
        sig = _make_signature(t.get("dealId"), t.get("dealReference"), t.get("ticker"), t.get("entry_price"))
        existing_signatures.add(sig)

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
                    "time_entered_human": _humanize(time_entered or _now_iso()),
                    "time_exited": None,
                    "time_exited_human": None,
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
                    "time_entered_human": _humanize(time_entered or _now_iso()),
                    "time_exited": None,
                    "time_exited_human": None,
                    "pnl": None,
                    "pnl_gbp": None,
                    "status": "OPEN",
                    "notes": "Imported from live positions (partial)"
                }
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
        return upsert_open_trade(args[0])
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
    return upsert_open_trade(payload)

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
