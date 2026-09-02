#!/usr/bin/env python3
# trade_log.py
# Unified, thread-safe trade log manager with environment FX sync.
# - canonical upsert for open trades
# - centralized close logic computing pnl and pnl_gbp (reads FX from env at compute time)
# - preserves ISO timestamps internally; adds human fields for display
# - rejects malformed appends instead of defaulting entry_price to 0
# - syncs config.FX_USD_GBP into environment at import time if present
# - thread-safe via module-level lock (all read-modify-write operations are protected)

import json
import os
import tempfile
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import logging

# Try to import config and, if present, ensure FX_USD_GBP is available in the environment.
try:
    import config  # type: ignore
    try:
        if getattr(config, "FX_USD_GBP", None) is not None:
            os.environ.setdefault("FX_USD_GBP", str(config.FX_USD_GBP))
    except Exception:
        pass
except Exception:
    config = None  # type: ignore

# Optional timezone support
try:
    import pytz  # type: ignore
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

# Float comparison tolerance for matching entry prices
FLOAT_TOLERANCE = 1e-8

# Maximum number of timestamped backup files to retain
MAX_BACKUP_VERSIONS = 5

# Valid side/direction values
VALID_SIDES = frozenset(("buy", "sell", "long", "short"))

# FX rate sanity range (USD/GBP should be roughly 0.5–1.5)
FX_RATE_MIN = 0.5
FX_RATE_MAX = 1.5

# Module-level lock protecting all read-modify-write operations on the trade log file.
# Any function that calls load_raw_log() then save_raw_log() must acquire this lock first.
_trade_log_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current time as ISO-8601 string."""
    now = datetime.utcnow()
    if UK_TZ:
        try:
            return datetime.now(UK_TZ).isoformat()
        except Exception:
            pass
    return now.isoformat() + "Z"


def _atomic_write(path: str, data: Any) -> bool:
    """Write *data* as JSON to *path* atomically via a temp file + rename."""
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
    """Parse an ISO-like datetime string; returns None if unparseable."""
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
    """Convert an ISO timestamp string to a human-readable display string."""
    d = _parse_iso_like(dt_str)
    if not d:
        return None
    return d.strftime("%d-%m-%Y %H:%M:%S")


def _read_fx_rate() -> float:
    """Read FX rate (USD -> GBP) from environment with validation.

    Validates that the rate is within a reasonable range (FX_RATE_MIN–FX_RATE_MAX).
    Falls back to 0.738 when the value is absent or out of range.
    """
    default = 0.738
    try:
        val = os.environ.get("FX_USD_GBP", None)
        if val is None and config is not None:
            cfg_val = getattr(config, "FX_USD_GBP", None)
            if cfg_val is not None:
                val = str(cfg_val)
        if val is not None:
            rate = float(val)
            if FX_RATE_MIN <= rate <= FX_RATE_MAX:
                return rate
            logger.warning("FX_USD_GBP value %s is outside expected range [%s, %s]; using default %s",
                           rate, FX_RATE_MIN, FX_RATE_MAX, default)
        return default
    except Exception:
        return default


def _rotate_backups(base_backup_path: str) -> None:
    """Keep the last MAX_BACKUP_VERSIONS timestamped backup files."""
    backup_dir = os.path.dirname(base_backup_path) or "."
    stem = os.path.basename(base_backup_path)
    try:
        all_backups = sorted([
            os.path.join(backup_dir, f)
            for f in os.listdir(backup_dir)
            if f.startswith(stem.replace(".json", "")) and f.endswith(".json") and f != stem
        ])
        while len(all_backups) >= MAX_BACKUP_VERSIONS:
            oldest = all_backups.pop(0)
            try:
                os.remove(oldest)
            except Exception:
                pass
    except Exception:
        pass


def _normalize_side(side: Any) -> Optional[str]:
    if side is None:
        return None
    s = str(side).strip().lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s or None


def _detect_trade_origin(payload: Dict[str, Any], side: Optional[str], dealId: Any, dealReference: Any) -> str:
    raw_origin = payload.get("origin") or payload.get("source") or payload.get("trade_source") or payload.get("tradeSource")
    if raw_origin:
        return str(raw_origin).strip().lower()
    if payload.get("webhook") is True or payload.get("cid") or payload.get("alert_id"):
        return "webhook"
    if payload.get("manual") is True:
        return "manual"
    if dealId is not None or dealReference is not None:
        return "bot"
    if side in ("long", "short"):
        return "manual"
    return "unknown"


# ---------------------------------------------------------------------------
# Public I/O helpers
# ---------------------------------------------------------------------------

def load_raw_log(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Load the trade log from *path*.

    Returns an empty list when the file is absent or contains invalid JSON.
    Enriches each entry with human-readable timestamp fields and pnl_gbp.
    """
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
    """Persist *trades* to *path* with a timestamped backup of the previous version.

    Uses an atomic write (temp-file + rename) to prevent partial writes.
    """
    try:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    old = f.read()
                ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                stem, ext = os.path.splitext(BACKUP_PATH)
                versioned_backup = f"{stem}_{ts}{ext}"
                with open(versioned_backup, "w", encoding="utf-8") as bf:
                    bf.write(old)
                _rotate_backups(versioned_backup)
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
    """Overwrite the trade log with an empty list."""
    return save_raw_log([], path)


# ---------------------------------------------------------------------------
# Internal calculation helpers
# ---------------------------------------------------------------------------

def _float_equal(a: Any, b: Any, tol: float = FLOAT_TOLERANCE) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return str(a) == str(b)


def _compute_pnl_for_trade(trade: Dict[str, Any]) -> Optional[float]:
    """Compute P&L for a closed trade."""
    entry_raw = trade.get("entry_price")
    exit_raw = trade.get("exit_price")
    size_raw = trade.get("size", 0)

    if entry_raw is None:
        logger.warning("trade_log: _compute_pnl_for_trade – missing entry_price for trade %s",
                       trade.get("dealId") or trade.get("ticker"))
        return None
    if exit_raw is None:
        logger.warning("trade_log: _compute_pnl_for_trade – missing exit_price for trade %s",
                       trade.get("dealId") or trade.get("ticker"))
        return None

    try:
        entry = float(entry_raw)
        exitp = float(exit_raw)
        size = float(size_raw)
        side = _normalize_side(trade.get("side"))
        if side == "long":
            pnl = (exitp - entry) * size
        elif side == "short":
            pnl = (entry - exitp) * size
        else:
            pnl = (exitp - entry) * size
        return round(pnl, 2)
    except Exception:
        logger.exception("trade_log: failed to compute pnl for trade %s",
                         trade.get("dealId") or trade.get("ticker"))
        return None


def _make_signature(dealId: Any, dealReference: Any, ticker: Any, entry_price: Any) -> str:
    try:
        entry_norm = round(float(entry_price or 0), 8)
    except Exception:
        entry_norm = str(entry_price)
    return f"{dealId or ''}|{dealReference or ''}|{ticker or ''}|{entry_norm}"


def _find_pending_trade_by_ticker(trades: List[Dict[str, Any]], ticker: Any,
                                   side: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Find a still-open trade that has no dealId yet for *ticker* (optionally *side*).

    This is used as a fallback match when a broker-confirmed dealId/entry_price/size
    doesn't exactly line up with the values recorded at order-placement time (e.g. a
    requested size of 6.8 gets filled by the broker as 6.83, or the entry price moved
    slightly between the market snapshot used for sizing and the actual fill). Without
    this fallback, the mismatch causes a brand-new duplicate trade-log entry to be
    created for the same real position, leaving the original entry (still lacking a
    dealId) stuck open forever once the real position closes.
    """
    if not ticker:
        return None
    ticker_norm = str(ticker).strip().lower()
    candidates = [
        t for t in trades
        if t.get("status") != "CLOSED" and not t.get("dealId")
        and t.get("ticker") and str(t.get("ticker")).strip().lower() == ticker_norm
    ]
    if side:
        side_norm = _normalize_side(side)
        if side_norm:
            narrowed = [t for t in candidates if not t.get("side") or _normalize_side(t.get("side")) == side_norm]
            if narrowed:
                candidates = narrowed
    if not candidates:
        return None
    candidates.sort(key=lambda t: str(t.get("time_entered") or ""), reverse=True)
    return candidates[0]


def _find_open_trade_by_ticker_any_dealid(trades: List[Dict[str, Any]], ticker: Any,
                                           side: Optional[str], dealId: Any) -> Optional[Dict[str, Any]]:
    """Find any still-open trade for *ticker* (+ *side*) other than one already
    carrying *dealId*, regardless of whether it has a dealId of its own.

    Used as a last-resort fallback in reconcile_with_positions() before
    creating a brand-new log entry for a broker-reported position. Without
    this, a live position whose dealId doesn't exactly match any known dealId
    or pending (dealId-less) entry – e.g. because the local trade was already
    reconciled/mapped under a slightly different dealId, or was opened outside
    the normal order.place_order() flow – gets logged as a second, duplicate
    entry for a ticker that already has a genuine open position, instead of
    being recognised as the same real trade.
    """
    if not ticker:
        return None
    ticker_norm = str(ticker).strip().lower()
    dealId_norm = str(dealId) if dealId is not None else None
    candidates = [
        t for t in trades
        if t.get("status") != "CLOSED"
        and t.get("ticker") and str(t.get("ticker")).strip().lower() == ticker_norm
        and (dealId_norm is None or str(t.get("dealId")) != dealId_norm)
    ]
    if side:
        side_norm = _normalize_side(side)
        if side_norm:
            narrowed = [t for t in candidates if not t.get("side") or _normalize_side(t.get("side")) == side_norm]
            if narrowed:
                candidates = narrowed
    if not candidates:
        return None
    candidates.sort(key=lambda t: str(t.get("time_entered") or ""), reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Core public API
# ---------------------------------------------------------------------------

def find_trade(dealId: Optional[str] = None, dealReference: Optional[str] = None,
               ticker: Optional[str] = None, entry_price: Optional[float] = None,
               path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    """Find a trade by dealId, dealReference, or signature match."""
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


def validate_trade_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate required trade fields."""
    pos = payload.get("position") or payload.get("raw") or payload
    market = payload.get("market") or (pos.get("market") if isinstance(pos, dict) else None)

    entry_price = payload.get("entry_price") or (pos.get("level") if isinstance(pos, dict) else None) or (pos.get("entryPrice") if isinstance(pos, dict) else None)
    size = payload.get("size") or (pos.get("size") if isinstance(pos, dict) else None) or (pos.get("contractSize") if isinstance(pos, dict) else None)
    side = payload.get("side") or (pos.get("direction") if isinstance(pos, dict) else None)

    try:
        ep = float(entry_price) if entry_price not in (None, "") else None
    except Exception:
        ep = None
    if ep is None:
        return False, f"missing_entry_price (got: {entry_price!r})"
    if ep <= 0:
        return False, f"entry_price_not_positive (got: {ep})"

    try:
        sz = float(size) if size not in (None, "") else None
    except Exception:
        sz = None
    if sz is None:
        return False, f"missing_size (got: {size!r})"
    if sz <= 0:
        return False, f"size_not_positive (got: {sz})"

    if side is not None:
        if str(side).strip().lower() not in VALID_SIDES:
            return False, f"invalid_side (got: {side!r}, expected one of {sorted(VALID_SIDES)})"

    return True, ""


def upsert_open_trade(payload: Dict[str, Any], path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    """Insert or update an open trade record."""
    pos = payload.get("position") or payload.get("raw") or payload
    market = payload.get("market") or (pos.get("market") if isinstance(pos, dict) else None)

    dealId = payload.get("dealId") or (pos.get("dealId") if isinstance(pos, dict) else None)
    dealReference = payload.get("dealReference") or (pos.get("dealReference") if isinstance(pos, dict) else None)
    ticker = payload.get("ticker") or (market.get("symbol") if isinstance(market, dict) else None) or (pos.get("instrument") if isinstance(pos, dict) else None)
    side = _normalize_side(payload.get("side") or (pos.get("direction") if isinstance(pos, dict) else None))
    size = payload.get("size") or (pos.get("size") if isinstance(pos, dict) else None) or (pos.get("contractSize") if isinstance(pos, dict) else None)
    entry_price = payload.get("entry_price") or (pos.get("level") if isinstance(pos, dict) else None) or (pos.get("entryPrice") if isinstance(pos, dict) else None)
    time_entered = payload.get("time_entered") or (pos.get("createdDate") if isinstance(pos, dict) else None) or (pos.get("createdDateUTC") if isinstance(pos, dict) else None)
    origin = _detect_trade_origin(payload, side, dealId, dealReference)

    valid, reason = validate_trade_payload({**payload, "side": side})
    if not valid:
        logger.warning("upsert_open_trade: validation failed – %s (dealId=%s dealRef=%s ticker=%s)",
                       reason, dealId, dealReference, ticker)
        return None

    try:
        size_val = float(size)
        entry_val = float(entry_price)
    except Exception:
        logger.warning("upsert_open_trade: could not coerce size/entry_price to float")
        return None

    with _trade_log_lock:
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

        matched_via_pending = False
        if not existing and dealId:
            # A broker-confirmed dealId didn't exactly match any existing entry
            # (e.g. differing entry_price/size due to slippage or fill rounding).
            # Fall back to matching a still-pending trade for the same ticker
            # instead of creating a duplicate open-position entry.
            existing = _find_pending_trade_by_ticker(trades, ticker, side)
            matched_via_pending = existing is not None

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
            if (existing.get("size") in (None, 0)) and size_val > 0:
                existing["size"] = size_val; updated = True
            if (existing.get("entry_price") in (None, "")) and entry_val > 0:
                existing["entry_price"] = entry_val; updated = True
            if matched_via_pending:
                # The broker-confirmed values are the source of truth; correct any
                # earlier estimate (e.g. requested size 6.8 filled as 6.83) so the
                # log reflects the real position instead of leaving it mismatched.
                if size_val > 0 and not _float_equal(existing.get("size"), size_val):
                    existing["size"] = size_val; updated = True
                if entry_val > 0 and not _float_equal(existing.get("entry_price"), entry_val):
                    existing["entry_price"] = entry_val; updated = True
            if (existing.get("time_entered") in (None, "")) and time_entered:
                existing["time_entered"] = time_entered; updated = True
            if not existing.get("trade_source"):
                existing["trade_source"] = existing.get("origin") or origin
                updated = True
            elif origin and not existing.get("origin"):
                existing["origin"] = origin
                updated = True
            if updated:
                existing["time_entered_human"] = _humanize(existing.get("time_entered"))
                save_raw_log(trades, path)
            return existing

        new_trade = {
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
            "trade_source": origin,
            "origin": origin,
            "notes": payload.get("notes") or "Imported"
        }
        trades.append(new_trade)
        if save_raw_log(trades, path):
            return new_trade
        return None


def close_trade_by_dealId(dealId: Any, exit_price: Any = None, time_exited: Optional[str] = None,
                           note: Optional[str] = None, path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    """Mark the open trade with *dealId* as CLOSED and compute P&L."""
    with _trade_log_lock:
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


def close_trade_fallback(ticker: Any, entry_price: Any, exit_price: Any = None,
                          time_exited: Optional[str] = None, note: Optional[str] = None,
                          path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    """Close the first open trade matching *ticker* + *entry_price* (fuzzy)."""
    with _trade_log_lock:
        trades = load_raw_log(path)
        updated = None
        candidates = []
        for t in trades:
            if t.get("status") != "CLOSED" and t.get("ticker") == ticker:
                if _float_equal(t.get("entry_price", 0), entry_price):
                    candidates.append(t)
        if not candidates:
            return None
        candidates.sort(key=lambda t: str(t.get("time_entered") or ""), reverse=True)
        t = candidates[0]
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
        if updated:
            save_raw_log(trades, path)
        return updated


def set_dealId_for_dealReference(dealReference: Any, dealId: Any, path: str = LOG_PATH) -> bool:
    """Back-fill *dealId* on a trade previously recorded only by *dealReference*."""
    if not dealReference or not dealId:
        return False
    with _trade_log_lock:
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
    """Reconcile local trade log against live broker positions."""
    with _trade_log_lock:
        trades = load_raw_log(path)
        closed: List[Dict[str, Any]] = []
        added: List[Dict[str, Any]] = []
        matched_updates: List[Dict[str, Any]] = []

        reopened: List[Dict[str, Any]] = []

        live_ids = set()
        live_positions_by_id = {}
        for p in live_positions or []:
            did = None
            if isinstance(p, dict):
                did = p.get("dealId") or (p.get("position") or {}).get("dealId")
            if did is not None:
                sdid = str(did)
                live_ids.add(sdid)
                live_positions_by_id[sdid] = p

        # Self-heal: if a trade was previously (and incorrectly) marked CLOSED —
        # e.g. by a webhook "close" alert whose exit_price/dealId didn't reflect an
        # actual broker-side close — but the broker still reports that dealId as an
        # open live position, the broker is the source of truth. Revert the trade
        # to OPEN so it doesn't appear simultaneously as an open position and as a
        # duplicate completed trade in the log/analytics.
        if live_ids:
            for t in trades:
                if t.get("status") == "CLOSED":
                    did = t.get("dealId")
                    if did is not None and str(did) in live_ids:
                        t["status"] = "OPEN"
                        t["exit_price"] = None
                        t["time_exited"] = None
                        t["time_exited_human"] = None
                        t["pnl"] = None
                        t["pnl_gbp"] = None
                        t["notes"] = (t.get("notes") or "") + " | Reopened: broker still reports this position open"
                        reopened.append(t)

        for t in trades:
            if t.get("status") != "CLOSED":
                did = t.get("dealId")
                if did is not None and str(did) not in live_ids:
                    p = live_positions_by_id.get(str(did))
                    exit_price = None
                    time_exited = None
                    if isinstance(p, dict):
                        exit_price = p.get("exit_price") or p.get("closePrice") or p.get("closedPrice") or p.get("price")
                        time_exited = p.get("time_exited") or p.get("timeExited") or p.get("closedDate")
                    if exit_price is None:
                        continue
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
            trade_source = "unknown"

            if isinstance(p, dict):
                if p.get("dealId") is not None:
                    dealId = p.get("dealId")
                    dealReference = p.get("dealReference")
                    ticker = p.get("ticker") or p.get("epic")
                    entry_price = p.get("price") or p.get("entry_price") or p.get("level")
                    side = _normalize_side(p.get("side") or p.get("direction"))
                    size = p.get("size")
                    time_entered = p.get("time_entered") or p.get("createdDate")
                    trade_source = _detect_trade_origin(p, side, dealId, dealReference)
                else:
                    pos = p.get("position") or {}
                    market = p.get("market") or {}
                    dealId = pos.get("dealId") or pos.get("dealReference")
                    dealReference = pos.get("dealReference") or p.get("dealReference")
                    ticker = market.get("symbol") or pos.get("instrumentName") or pos.get("instrument")
                    entry_price = pos.get("level") or pos.get("price") or pos.get("entry_price")
                    side = _normalize_side(pos.get("direction"))
                    size = pos.get("size")
                    time_entered = pos.get("createdDate") or pos.get("time_entered")
                    trade_source = _detect_trade_origin(p, side, dealId, dealReference)

            sig = _make_signature(dealId, dealReference, ticker, entry_price)
            if sig in existing_signatures:
                continue

            # Try to match an existing entry by dealId alone (ignoring entry_price
            # differences caused by slippage) before falling back to a still-pending
            # (dealId-less) entry for the same ticker. Either match is corrected in
            # place with the broker-confirmed size/entry_price rather than creating a
            # duplicate open-position entry that would never get closed.
            matched = None
            if dealId:
                for t in trades:
                    if t.get("dealId") and str(t.get("dealId")) == str(dealId):
                        matched = t
                        break
            if matched is None and dealId:
                matched = _find_pending_trade_by_ticker(trades, ticker, side)
            if matched is None:
                # Last resort: an already-open trade for this ticker/side exists
                # but wasn't matched above (e.g. it already carries a different
                # dealId). Merge into it instead of creating a duplicate entry
                # for what is almost certainly the same real position.
                matched = _find_open_trade_by_ticker_any_dealid(trades, ticker, side, dealId)

            if matched is not None:
                changed = False
                if not matched.get("dealId") and dealId:
                    matched["dealId"] = dealId; changed = True
                if not matched.get("dealReference") and dealReference:
                    matched["dealReference"] = dealReference; changed = True
                try:
                    if size not in (None, "", 0) and not _float_equal(matched.get("size"), size):
                        matched["size"] = float(size); changed = True
                except Exception:
                    pass
                try:
                    if entry_price not in (None, "", 0) and not _float_equal(matched.get("entry_price"), entry_price):
                        matched["entry_price"] = float(entry_price); changed = True
                except Exception:
                    pass
                if changed:
                    existing_signatures.add(_make_signature(matched.get("dealId"), matched.get("dealReference"), matched.get("ticker"), matched.get("entry_price")))
                    matched_updates.append(matched)
                continue

            try:
                new_pos = {
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
                    "trade_source": trade_source,
                    "origin": trade_source,
                    "notes": "Imported from live positions"
                }
            except Exception:
                new_pos = {
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
                    "trade_source": trade_source,
                    "origin": trade_source,
                    "notes": "Imported from live positions (partial)"
                }
            trades.append(new_pos)
            added.append(new_pos)
            existing_signatures.add(sig)

        if closed or added or matched_updates or reopened:
            save_raw_log(trades, path)

    return {"closed": closed, "added": added, "reopened": reopened}


def get_completed_trades(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Return all CLOSED trades from the log."""
    trades = load_raw_log(path)
    return [t for t in trades if t.get("status") == "CLOSED"]


def get_open_trades(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Return all non-CLOSED trades from the log."""
    trades = load_raw_log(path)
    return [t for t in trades if t.get("status") != "CLOSED"]


# ---------------------------------------------------------------------------
# Backwards compatibility wrappers
# ---------------------------------------------------------------------------

def log_open_trade(*args, **kwargs) -> Optional[Dict[str, Any]]:
    """Compatibility wrapper – delegates to upsert_open_trade."""
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
    """Compatibility wrapper – delegates to close_trade_by_dealId or close_trade_fallback."""
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


def append_open_trade(*args, **kwargs) -> Optional[Dict[str, Any]]:
    """Compatibility wrapper kept for older modules that import append_open_trade.

    Delegates to the canonical upsert_open_trade/log_open_trade API.
    """
    try:
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
    except Exception:
        return None


def get_trades(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Return all trades from the log."""
    return load_raw_log(path)
