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

# Only collapse trades into one row when they look like the *same* logical
# trade recorded twice within a short window (e.g. webhook + reconcile race).
# Capital.com can reuse a dealId across unrelated historical trades, so any
# dedupe keyed on dealId alone must still require corroborating fields/time.
DUPLICATE_TRADE_TIME_WINDOW_SECONDS = 15 * 60

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


def _canonical_trade_source(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    source = str(value).strip().lower()
    if source in ("tradingview", "webhook", "bot"):
        return "tradingview"
    if source in ("manual", "broker", "trader"):
        return "trader"
    if source in ("hedge",):
        return "hedge"
    if source in ("unknown",):
        return "unknown"
    return source or None


def _detect_trade_origin(payload: Dict[str, Any], side: Optional[str], dealId: Any, dealReference: Any) -> str:
    raw_origin = _canonical_trade_source(
        payload.get("origin") or payload.get("source") or payload.get("trade_source") or payload.get("tradeSource")
    )
    if raw_origin:
        return raw_origin
    if payload.get("webhook") is True or payload.get("cid") or payload.get("alert_id"):
        return "tradingview"
    if payload.get("manual") is True:
        return "trader"
    if dealId is not None or dealReference is not None:
        return "trader"
    if side in ("long", "short"):
        return "trader"
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


def delete_completed_trade(index: int, path: str = LOG_PATH) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Delete one completed trade row from the raw log by its zero-based index."""
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return False, None, "invalid_index"

    with _trade_log_lock:
        trades = load_raw_log(path)
        if idx < 0 or idx >= len(trades):
            return False, None, "not_found"

        trade = trades[idx] if isinstance(trades[idx], dict) else {}
        status = str(trade.get("status") or "").strip().upper()
        is_completed = status == "CLOSED" or trade.get("time_exited") not in (None, "")
        if not is_completed:
            return False, None, "not_completed"

        deleted = dict(trade)
        trades.pop(idx)
        if not save_raw_log(trades, path):
            return False, None, "save_failed"
        return True, deleted, "deleted"


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


def _coerce_trade_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _dealid_is_placeholder(deal_id: Any, deal_reference: Any) -> bool:
    """Return True when *deal_id* is just a temporary echo of dealReference."""
    if deal_id in (None, "") or deal_reference in (None, ""):
        return False
    return str(deal_id) == str(deal_reference)


def _times_within_window(a: Optional[str], b: Optional[str],
                         window_seconds: int = DUPLICATE_TRADE_TIME_WINDOW_SECONDS) -> bool:
    dt_a = _parse_iso_like(a)
    dt_b = _parse_iso_like(b)
    if dt_a is None or dt_b is None:
        return False
    try:
        return abs((dt_a - dt_b).total_seconds()) <= window_seconds
    except Exception:
        return False


def _trade_merge_score(trade: Dict[str, Any]) -> int:
    score = 0
    if trade.get("dealReference"):
        score += 16
    if trade.get("dealId"):
        score += 12
    if trade.get("status") == "CLOSED":
        score += 8
    if trade.get("time_exited"):
        score += 6
    if trade.get("exit_price") not in (None, ""):
        score += 5
    if trade.get("pnl") not in (None, ""):
        score += 4
    if trade.get("time_entered"):
        score += 3
    if trade.get("ticker"):
        score += 2
    if trade.get("trade_source") not in (None, "", "unknown"):
        score += 1
    return score


def _select_timestamp(entries: List[Dict[str, Any]], key: str, pick_latest: bool) -> Optional[str]:
    best = None
    for entry in entries:
        raw = entry.get(key)
        parsed = _parse_iso_like(raw)
        if parsed is None:
            continue
        if best is None:
            best = (parsed, raw)
            continue
        if (pick_latest and parsed > best[0]) or (not pick_latest and parsed < best[0]):
            best = (parsed, raw)
    return best[1] if best else None


def _is_probable_duplicate_trade(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    ref_a = a.get("dealReference")
    ref_b = b.get("dealReference")
    if ref_a and ref_b:
        return str(ref_a) == str(ref_b)

    deal_a = a.get("dealId")
    deal_b = b.get("dealId")
    if not (deal_a and deal_b) or str(deal_a) != str(deal_b):
        return False

    side_a = _normalize_side(a.get("side"))
    side_b = _normalize_side(b.get("side"))
    if side_a and side_b and side_a != side_b:
        return False

    entry_a = _coerce_trade_float(a.get("entry_price"))
    entry_b = _coerce_trade_float(b.get("entry_price"))
    if entry_a is not None and entry_b is not None and not _float_equal(entry_a, entry_b):
        return False

    size_a = _coerce_trade_float(a.get("size"))
    size_b = _coerce_trade_float(b.get("size"))
    if size_a is not None and size_b is not None and not _float_equal(size_a, size_b):
        return False

    exit_a = _coerce_trade_float(a.get("exit_price"))
    exit_b = _coerce_trade_float(b.get("exit_price"))
    if exit_a is not None and exit_b is not None and not _float_equal(exit_a, exit_b):
        return False

    if _times_within_window(a.get("time_entered"), b.get("time_entered")):
        return True
    if _times_within_window(a.get("time_exited"), b.get("time_exited")):
        return True

    # Last-resort guard for duplicate rows created from the same close event
    # where one side lost timestamps but the other fields still line up.
    return (
        entry_a is not None and entry_b is not None
        and size_a is not None and size_b is not None
        and exit_a is not None and exit_b is not None
        and (
            not a.get("time_entered") or not b.get("time_entered")
            or not a.get("time_exited") or not b.get("time_exited")
        )
    )


def _merge_trade_group(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    ranked = sorted(entries, key=_trade_merge_score, reverse=True)
    merged = dict(ranked[0])

    notes_parts: List[str] = []
    seen_notes = set()
    for entry in ranked:
        note = (entry.get("notes") or "").strip()
        if note and note not in seen_notes:
            seen_notes.add(note)
            notes_parts.append(note)

    for entry in ranked[1:]:
        for key in ("dealId", "dealReference", "ticker", "side", "size", "entry_price",
                    "exit_price", "trade_source", "origin"):
            if merged.get(key) in (None, "") and entry.get(key) not in (None, ""):
                merged[key] = entry.get(key)

    earliest_entered = _select_timestamp(ranked, "time_entered", pick_latest=False)
    latest_exited = _select_timestamp(ranked, "time_exited", pick_latest=True)
    if earliest_entered:
        merged["time_entered"] = earliest_entered
        merged["time_entered_human"] = _humanize(earliest_entered)
    if latest_exited:
        merged["time_exited"] = latest_exited
        merged["time_exited_human"] = _humanize(latest_exited)

    if any(entry.get("status") == "CLOSED" for entry in ranked):
        merged["status"] = "CLOSED"
        broker_pnl = next((entry.get("pnl") for entry in ranked if entry.get("pnl") not in (None, "")), None)
        _apply_pnl(merged, broker_pnl=broker_pnl)

    if notes_parts:
        merged["notes"] = " | ".join(notes_parts)

    return merged


def dedupe_trade_log_entries(trades: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    """Collapse obviously-duplicate trade-log rows without merging reused dealIds.

    Two rows are treated as the same logical trade only when they share a
    stable identifier (dealReference, or dealId plus matching size/price/side)
    and their timestamps corroborate that they were recorded during the same
    open/close event. This intentionally avoids deduping on bare dealId alone,
    since the broker can recycle dealIds across unrelated historical trades.
    """
    groups: List[List[Dict[str, Any]]] = []
    for trade in trades or []:
        matched_group = None
        for group in groups:
            if any(_is_probable_duplicate_trade(existing, trade) for existing in group):
                matched_group = group
                break
        if matched_group is None:
            groups.append([trade])
        else:
            matched_group.append(trade)

    changed = any(len(group) > 1 for group in groups)
    if not changed:
        return list(trades or []), False

    deduped = []
    for group in groups:
        deduped.append(_merge_trade_group(group) if len(group) > 1 else group[0])
    return deduped, True


def canonicalize_trade_log(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Persistently collapse obvious duplicate rows in the canonical log file."""
    with _trade_log_lock:
        trades = load_raw_log(path)
        deduped, changed = dedupe_trade_log_entries(trades)
        if changed:
            save_raw_log(deduped, path)
            return deduped
        return trades


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

        if not existing and not dealId and dealReference:
            # Last-resort fallback: this payload has a dealReference but no
            # dealId yet (order.py's post-order log-append always logs this
            # way until the confirms endpoint later maps the dealId in via
            # set_dealId_for_dealReference). If a concurrent poll of
            # reconcile_with_positions() already picked up the live broker
            # position - with its real dealId - before this call ran, none of
            # the matches above would find it (they all require *this*
            # payload to already carry a dealId), causing a duplicate row to
            # be created for the same real position. Merge into any still-open
            # trade for the same ticker/side instead. Do not treat this as
            # matched_via_pending: an entry found this way may already hold
            # broker-confirmed size/entry_price values that must not be
            # overwritten by this payload's own (possibly estimated) figures.
            existing = _find_open_trade_by_ticker_any_dealid(trades, ticker, side, dealId)

        if existing:
            updated = False
            if dealId and (
                not existing.get("dealId")
                or _dealid_is_placeholder(existing.get("dealId"), existing.get("dealReference"))
                or (dealReference not in (None, "") and str(existing.get("dealId")) == str(dealReference))
            ):
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


def _apply_pnl(t: Dict[str, Any], broker_pnl: Any = None) -> None:
    """Set t['pnl']/t['pnl_gbp'] on a just-closed trade.

    Prefers *broker_pnl* (the authoritative profit/loss figure reported by
    Capital.com's transaction history) when supplied, since it reflects the
    actual fill/exec prices, spread, and any fees – all of which the naive
    (exit_price - entry_price) * size estimate below ignores. That estimate
    is used only as a fallback when the broker hasn't reported a PnL figure
    (e.g. exit_price came from a live market snapshot rather than a
    confirmed closing transaction).
    """
    if broker_pnl is not None:
        try:
            t["pnl"] = round(float(broker_pnl), 2)
        except Exception:
            t["pnl"] = None
    elif t.get("exit_price") is not None:
        t["pnl"] = _compute_pnl_for_trade(t)
    else:
        t["pnl"] = None

    if t.get("pnl") not in (None, ""):
        try:
            fx = _read_fx_rate()
            t["pnl_gbp"] = round(float(t["pnl"]) * fx, 2)
        except Exception:
            t["pnl_gbp"] = None
    else:
        t["pnl_gbp"] = None


def close_trade_by_dealId(dealId: Any, exit_price: Any = None, time_exited: Optional[str] = None,
                           note: Optional[str] = None, pnl: Any = None, path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    """Mark the open trade(s) with *dealId* as CLOSED and compute P&L.

    If *pnl* (the broker-confirmed profit/loss) is supplied, it is used
    directly instead of being recomputed locally from entry/exit price and
    size, so the logged figure matches what actually happened on the broker.

    Closes *every* still-OPEN entry carrying this dealId, not just the first
    one found. Under normal operation there is only ever one such entry, but
    if a duplicate row for the same real position ever slips into the log
    (e.g. an older bug in reconcile_with_positions()'s ticker matching), only
    closing the first match left the duplicate stuck OPEN forever – every
    later poll would skip it because its dealId was already present among
    CLOSED entries (sync_closed_trades()'s closed_ids/_last_close_cache
    guards are dealId-keyed, not row-keyed). Closing all matches here means a
    stray duplicate is resolved (and visually deduplicated by the dashboard,
    which prefers CLOSED over OPEN for identical dealIds) rather than
    lingering as a phantom open position indefinitely.
    """
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
                _apply_pnl(t, broker_pnl=pnl)
                if note:
                    t["notes"] = (t.get("notes") or "") + " | " + note
                if updated is None:
                    updated = t
        if updated:
            trades, _ = dedupe_trade_log_entries(trades)
            save_raw_log(trades, path)
            for t in trades:
                if t.get("dealId") is not None and str(t.get("dealId")) == str(dealId) and t.get("status") == "CLOSED":
                    return t
        return None


def close_trade_fallback(ticker: Any, entry_price: Any, exit_price: Any = None,
                          time_exited: Optional[str] = None, note: Optional[str] = None,
                          pnl: Any = None, path: str = LOG_PATH) -> Optional[Dict[str, Any]]:
    """Close the first open trade matching *ticker* + *entry_price* (fuzzy).

    See close_trade_by_dealId() for the meaning of *pnl*.
    """
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
        _apply_pnl(t, broker_pnl=pnl)
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
            if (
                t.get("dealReference") == dealReference
                and (
                    not t.get("dealId")
                    or _dealid_is_placeholder(t.get("dealId"), t.get("dealReference"))
                )
            ):
                t["dealId"] = dealId
                t["notes"] = (t.get("notes") or "") + f" | dealId_mapped={dealId}"
                updated = True
                break
        if updated:
            save_raw_log(trades, path)
        return updated


def reconcile_with_positions(live_positions: List[Dict[str, Any]], path: str = LOG_PATH) -> Dict[str, List[Dict[str, Any]]]:
    """Reconcile local trade log against live broker positions."""
    canonicalize_trade_log(path)
    with _trade_log_lock:
        trades = load_raw_log(path)
        closed: List[Dict[str, Any]] = []
        added: List[Dict[str, Any]] = []
        matched_updates: List[Dict[str, Any]] = []

        reopened: List[Dict[str, Any]] = []

        live_ids = set()
        for p in live_positions or []:
            did = None
            if isinstance(p, dict):
                did = p.get("dealId") or (p.get("position") or {}).get("dealId")
            if did is not None:
                live_ids.add(str(did))

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

        # Note: a still-open trade whose dealId is missing from the live
        # positions snapshot is *probably* closed, but the aggregate list can
        # transiently drop a still-open position. Reconcile deliberately does
        # NOT close trades here; the verified close (with exit price/PnL
        # confirmed against broker transaction history) is handled by
        # history_sync.sync_closed_trades.

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
                    # Match on the broker's stable epic code first, not the
                    # market's human-readable display symbol (e.g. "Seagate
                    # Technology" vs epic "STX"). Trades opened via the bot
                    # (order.py/webhook.py) are always logged with ticker=epic,
                    # so preferring the display symbol here made every such
                    # trade fail ticker-based matching below (_find_pending_
                    # trade_by_ticker / _find_open_trade_by_ticker_any_dealid),
                    # causing this loop to log a brand-new duplicate entry for
                    # a position that already had a pending/open row in the
                    # log instead of updating it in place.
                    ticker = p.get("epic") or p.get("ticker")
                    entry_price = p.get("price") or p.get("entry_price") or p.get("level")
                    side = _normalize_side(p.get("side") or p.get("direction"))
                    size = p.get("size")
                    time_entered = p.get("time_entered") or p.get("createdDate")
                    trade_source = _detect_trade_origin(p, side, dealId, dealReference)
                else:
                    pos = p.get("position") or {}
                    market = p.get("market") or {}
                    dealId = pos.get("dealId")
                    dealReference = pos.get("dealReference") or p.get("dealReference")
                    # Same rationale as above: prefer the epic code so this
                    # matches the ticker convention used by order.py/webhook.py.
                    ticker = market.get("epic") or market.get("symbol") or pos.get("instrumentName") or pos.get("instrument")
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
            if matched is not None:
                changed = False
                if dealId and (
                    not matched.get("dealId")
                    or _dealid_is_placeholder(matched.get("dealId"), matched.get("dealReference"))
                    or (dealReference not in (None, "") and str(matched.get("dealId")) == str(dealReference))
                ):
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

        trades, deduped = dedupe_trade_log_entries(trades)
        if closed or added or matched_updates or reopened or deduped:
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
