#!/usr/bin/env python3
# deposits.py
# Thread-safe manual bookkeeping ledger for deposits/withdrawals into the
# broker trading account. This module never moves real money — it only
# records entries the Owner enters by hand (e.g. "I wired £500 into the
# broker account on this date") so the dashboard can show accurate running
# deposit/withdrawal history alongside the live trading P&L.

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import logging

try:
    import config  # type: ignore
except Exception:
    config = None  # type: ignore

logger = logging.getLogger("deposits")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [deposits] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

LOG_PATH = os.environ.get("DEPOSITS_LOG_PATH") or (getattr(config, "DEPOSITS_LOG_PATH", None) if config else None) or "/data/deposits_log.json"

VALID_TYPES = frozenset(("deposit", "withdrawal"))

# Module-level lock protecting all read-modify-write operations on the deposits log file.
_deposits_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _atomic_write(path: str, data: Any) -> bool:
    """Write *data* as JSON to *path* atomically via a temp file + rename."""
    dirn = os.path.dirname(path) or "."
    try:
        os.makedirs(dirn, exist_ok=True)
    except Exception:
        pass
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


def load_entries(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Load the deposits/withdrawals ledger. Returns [] if absent/invalid."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                logger.warning("deposits: file content not a list, returning empty list")
                return []
            return data
    except Exception as exc:
        logger.exception("deposits: failed to load log: %s", exc)
        return []


def save_entries(entries: List[Dict[str, Any]], path: str = LOG_PATH) -> bool:
    """Persist *entries* to *path* via an atomic write."""
    ok = _atomic_write(path, entries)
    if not ok:
        logger.error("deposits: atomic write failed")
    return ok


def _coerce_amount(raw: Any) -> Optional[float]:
    try:
        amount = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return amount


def add_entry(
    entry_type: str,
    amount: Any,
    note: str = "",
    occurred_at: Optional[str] = None,
    path: str = LOG_PATH,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Append a new deposit/withdrawal row to the ledger.

    *amount* must be a positive number; direction is carried by *entry_type*
    ("deposit" or "withdrawal"), not by the sign of the amount.
    """
    normalized_type = str(entry_type or "").strip().lower()
    if normalized_type not in VALID_TYPES:
        return False, None, "invalid_type"

    amt = _coerce_amount(amount)
    if amt is None:
        return False, None, "invalid_amount"

    # Accept a caller-supplied date (YYYY-MM-DD or ISO datetime); fall back to now.
    when = (str(occurred_at).strip() if occurred_at else "") or _now_iso()

    record = {
        "id": uuid.uuid4().hex,
        "type": normalized_type,
        "amount": amt,
        "note": (str(note).strip() if note else "")[:500],
        "occurred_at": when,
        "created_at": _now_iso(),
    }

    with _deposits_lock:
        entries = load_entries(path)
        entries.append(record)
        if not save_entries(entries, path):
            return False, None, "save_failed"
        return True, record, "created"


def delete_entry(entry_id: str, path: str = LOG_PATH) -> Tuple[bool, str]:
    """Remove one ledger row by its ``id``."""
    target = str(entry_id or "").strip()
    if not target:
        return False, "invalid_id"

    with _deposits_lock:
        entries = load_entries(path)
        remaining = [e for e in entries if str(e.get("id")) != target]
        if len(remaining) == len(entries):
            return False, "not_found"
        if not save_entries(remaining, path):
            return False, "save_failed"
        return True, "deleted"


def summarize(entries: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute totals: total deposited, total withdrawn, and net contribution."""
    total_deposits = 0.0
    total_withdrawals = 0.0
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        amt = e.get("amount") or 0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            continue
        if e.get("type") == "deposit":
            total_deposits += amt
        elif e.get("type") == "withdrawal":
            total_withdrawals += amt
    return {
        "total_deposits": round(total_deposits, 2),
        "total_withdrawals": round(total_withdrawals, 2),
        "net": round(total_deposits - total_withdrawals, 2),
    }


def list_entries_sorted(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Return entries newest-first by ``occurred_at``."""
    entries = load_entries(path)

    def _sort_key(e: Dict[str, Any]) -> str:
        return str(e.get("occurred_at") or e.get("created_at") or "")

    return sorted(entries, key=_sort_key, reverse=True)
