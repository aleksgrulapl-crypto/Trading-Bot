#!/usr/bin/env python3
# deposits.py
# Thread-safe manual bookkeeping ledger for deposits/withdrawals into the
# broker trading account. This module never moves real money — it only
# records entries the Owner enters by hand (e.g. "I wired £500 into the
# broker account on this date") so the dashboard can show accurate running
# deposit/withdrawal history alongside the live trading P&L.

import json
import math
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import logging

try:
    import config  # type: ignore
except Exception:
    config = None  # type: ignore

# Optional timezone support — mirrors trade_log.py so deposit/pledge
# timestamps display in UK local time (GMT/BST) rather than raw UTC.
try:
    import pytz  # type: ignore
    UK_TZ = pytz.timezone("Europe/London")
except Exception:
    UK_TZ = None

logger = logging.getLogger("deposits")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [deposits] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

LOG_PATH = os.environ.get("DEPOSITS_LOG_PATH") or (getattr(config, "DEPOSITS_LOG_PATH", None) if config else None) or "/data/deposits_log.json"

VALID_TYPES = frozenset(("deposit", "withdrawal", "fee"))

# Fee entries aren't tied to a specific investor's capital — they're
# broker-level costs (e.g. Capital.com commission/financing charges) that
# reduce the fund as a whole. Entries of type "fee" are always assigned this
# label (any supplied investor name is ignored).
FEE_ENTRY_LABEL = "Broker"

# A deposit/withdrawal logged against this operator is a backend-cost entry,
# not an investor's capital: a withdrawal removes the same percentage of
# units from every investor (covering hosting/backend costs) and a deposit
# adds units to every investor the same way (balancing an over-estimated
# cost). The operator itself never holds units.
BACKEND_ENTRY_LABEL = "Backend"

# A backend cost the owner paid from their own pocket (the fund balance did
# not change). Every non-owner unit holder gives up units pro-rata to the
# cost and those units are credited to the owner; total units, fund value and
# NAV-per-unit are unchanged. A deposit reverses this.
BACKEND_OWNER_PAID_LABEL = "Backend (Owner paid)"


def is_owner_paid_backend(name: Any) -> bool:
    return str(name or "").strip().lower() == BACKEND_OWNER_PAID_LABEL.lower()


def is_backend_investor(name: Any) -> bool:
    n = str(name or "").strip().lower()
    return n == BACKEND_ENTRY_LABEL.lower() or n == BACKEND_OWNER_PAID_LABEL.lower()

# Module-level lock protecting all read-modify-write operations on the deposits log file.
_deposits_lock = threading.Lock()

# -----------------------------------------------------------------------
# Investor ROI tiers (Owner-only, changeable from the Investors page).
#
# "tradingview" (default): the investor only shares in PnL from
#   TradingView/Hedge (automated) trades.
# "full": the investor additionally shares pro-rata in "Trader"
#   (manual/discretionary) trade PnL, alongside the Owner.
#
# The Owner is always implicitly "full" tier and is never stored here —
# tier membership for the Owner is derived from OWNER_INVESTOR_NAME.
# -----------------------------------------------------------------------
TIERS_PATH = os.environ.get("INVESTOR_TIERS_PATH") or (getattr(config, "INVESTOR_TIERS_PATH", None) if config else None) or "/data/investor_tiers.json"

VALID_TIERS = frozenset(("tradingview", "full"))
DEFAULT_TIER = "tradingview"

_tiers_lock = threading.Lock()


def load_tiers(path: Optional[str] = None) -> Dict[str, str]:
    """Load the investor-tier map (lowercased investor name -> tier). Returns {} if absent/invalid."""
    path = path or TIERS_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("deposits: tiers file content not a dict, returning empty map")
                return {}
            return {str(k).strip().lower(): str(v) for k, v in data.items()}
    except Exception as exc:
        logger.exception("deposits: failed to load tiers: %s", exc)
        return {}


def save_tiers(tiers: Dict[str, str], path: Optional[str] = None) -> bool:
    """Persist *tiers* to *path* via an atomic write."""
    path = path or TIERS_PATH
    ok = _atomic_write(path, tiers)
    if not ok:
        logger.error("deposits: atomic write of tiers failed")
    return ok


def get_investor_tier(name: str, tiers: Optional[Dict[str, str]] = None, path: Optional[str] = None) -> str:
    """Return the stored tier for *name* ("tradingview" or "full"), defaulting
    to "tradingview" when unset/invalid. Lookup is case-insensitive."""
    key = str(name or "").strip().lower()
    if not key:
        return DEFAULT_TIER
    tiers = tiers if tiers is not None else load_tiers(path)
    tier = tiers.get(key)
    return tier if tier in VALID_TIERS else DEFAULT_TIER


def set_investor_tier(name: str, tier: str, path: Optional[str] = None) -> Tuple[bool, str]:
    """Set the ROI tier for investor *name*. Returns (ok, status)."""
    key = str(name or "").strip().lower()
    if not key:
        return False, "invalid_investor"
    tier = str(tier or "").strip().lower()
    if tier not in VALID_TIERS:
        return False, "invalid_tier"

    with _tiers_lock:
        tiers = load_tiers(path)
        tiers[key] = tier
        if not save_tiers(tiers, path):
            return False, "save_failed"
        return True, "updated"


def _now_iso() -> str:
    """Return the current time as an ISO-8601 string in UK local time
    (GMT/BST) when pytz is available, matching trade_log.py's behaviour."""
    if UK_TZ:
        try:
            return datetime.now(UK_TZ).isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


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


def _coerce_amount(raw: Any, allow_negative: bool = False) -> Optional[float]:
    try:
        amount = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    if amount == 0 or (amount < 0 and not allow_negative):
        return None
    return amount


def add_entry(
    entry_type: str,
    amount: Any,
    investor: str = "",
    note: str = "",
    occurred_at: Optional[str] = None,
    path: str = LOG_PATH,
    category: Optional[str] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Append a new deposit/withdrawal/fee row to the ledger.

    *amount* must be a positive number; direction is carried by *entry_type*
    ("deposit", "withdrawal", or "fee"), not by the sign of the amount.
    The one exception is a "fee" entry, where a negative amount is a credit
    (e.g. a positive Capital.com overnight funding adjustment).
    *investor* identifies whose capital this entry belongs to, so
    ROI/ownership share can be calculated per-person. Fee entries are a
    broker-level cost (e.g. Capital.com commission/financing charges)
    rather than one investor's capital, so *investor* may be left blank
    for them and is always set to ``FEE_ENTRY_LABEL`` ("Broker").
    """
    normalized_type = str(entry_type or "").strip().lower()
    if normalized_type not in VALID_TYPES:
        return False, None, "invalid_type"

    amt = _coerce_amount(amount, allow_negative=normalized_type == "fee")
    if amt is None:
        return False, None, "invalid_amount"

    investor_name = str(investor or "").strip()
    if normalized_type == "fee":
        investor_name = FEE_ENTRY_LABEL
    elif not investor_name:
        return False, None, "invalid_investor"
    investor_name = investor_name[:100]
    if is_owner_paid_backend(investor_name):
        investor_name = BACKEND_OWNER_PAID_LABEL
    elif is_backend_investor(investor_name):
        investor_name = BACKEND_ENTRY_LABEL

    # Accept a caller-supplied date (YYYY-MM-DD or ISO datetime); fall back to now.
    when = (str(occurred_at).strip() if occurred_at else "") or _now_iso()

    record = {
        "id": uuid.uuid4().hex,
        "type": normalized_type,
        "amount": amt,
        "investor": investor_name,
        "note": (str(note).strip() if note else "")[:500],
        "occurred_at": when,
        "created_at": _now_iso(),
    }
    if category:
        record["category"] = str(category)[:50]

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


def update_fee_amount(entry_id: str, amount: Any, path: str = LOG_PATH) -> Tuple[bool, str]:
    """Correct the amount of a recorded broker fee (e.g. an auto-detected
    overnight fee that was recorded wrong). Only fee entries are editable."""
    target = str(entry_id or "").strip()
    if not target:
        return False, "invalid_id"
    amt = _coerce_amount(amount, allow_negative=True)
    if amt is None:
        return False, "invalid_amount"

    with _deposits_lock:
        entries = load_entries(path)
        for e in entries:
            if str(e.get("id")) == target:
                if e.get("type") != "fee":
                    return False, "not_a_fee"
                e["amount"] = amt
                e["edited_at"] = _now_iso()
                if not save_entries(entries, path):
                    return False, "save_failed"
                return True, "updated"
        return False, "not_found"


def summarize(entries: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute totals: total deposited, total withdrawn, total fees, and net contribution."""
    total_deposits = 0.0
    total_withdrawals = 0.0
    total_fees = 0.0
    backend_deposits = 0.0
    backend_withdrawals = 0.0
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        amt = e.get("amount") or 0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            continue
        backend = is_backend_investor(e.get("investor"))
        if e.get("type") == "deposit":
            if backend:
                backend_deposits += amt
            else:
                total_deposits += amt
        elif e.get("type") == "withdrawal":
            if backend:
                backend_withdrawals += amt
            else:
                total_withdrawals += amt
        elif e.get("type") == "fee":
            total_fees += amt
    return {
        "total_deposits": round(total_deposits, 2),
        "total_withdrawals": round(total_withdrawals, 2),
        "total_fees": round(total_fees, 2),
        "backend_net_cost": round(backend_withdrawals - backend_deposits, 2),
        "net": round(total_deposits - total_withdrawals, 2),
    }


def fee_pnl_events(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build a ``{"ts": datetime, "pnl": -amount}`` event per recorded fee
    entry, so manually-logged broker fees (Capital.com commission/financing
    charges) can be merged into the same NAV-per-unit timeline as trade
    PnL in :func:`investor_breakdown`. This spreads each fee's cost across
    whoever holds fund units at the time it was incurred, rather than
    letting it silently show up as unexplained reconciliation drift
    credited entirely to the Owner."""
    events = []
    for e in entries or []:
        if not isinstance(e, dict) or e.get("type") != "fee":
            continue
        amt = e.get("amount") or 0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            continue
        ts = _parse_entry_datetime(e.get("occurred_at")) or _parse_entry_datetime(e.get("created_at"))
        if ts is None:
            continue
        events.append({"ts": ts, "pnl": -amt})
    return events


def list_entries_sorted(path: str = LOG_PATH) -> List[Dict[str, Any]]:
    """Return entries newest-first by ``occurred_at``."""
    entries = load_entries(path)

    def _sort_key(e: Dict[str, Any]) -> str:
        return str(e.get("occurred_at") or e.get("created_at") or "")

    return sorted(entries, key=_sort_key, reverse=True)


def list_investors(entries: List[Dict[str, Any]]) -> List[str]:
    """Return a sorted list of distinct investor names seen in *entries*
    (deposits/withdrawals only — fee entries are broker-level costs, not
    an investor's own capital, so they're excluded from this list)."""
    names = {
        str(e.get("investor") or "").strip()
        for e in entries or []
        if isinstance(e, dict) and e.get("type") in ("deposit", "withdrawal")
    }
    names.discard("")
    return sorted((n for n in names if not is_backend_investor(n)), key=str.lower)


def has_active_capital(name: Any, entries: List[Dict[str, Any]]) -> bool:
    """True when *name* has deposited and still has net capital in the fund."""
    net = 0.0
    deposited = False
    for e in entries or []:
        if not isinstance(e, dict) or not _same_name(e.get("investor"), name):
            continue
        try:
            amt = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if e.get("type") == "deposit":
            deposited = True
            net += amt
        elif e.get("type") == "withdrawal":
            net -= amt
    return deposited and net > 0.005


# -----------------------------------------------------------------------
# "Become an Investor" pledges.
#
# A pledge is just a note-to-self: "<name> intends to pay in <amount>".
# Submitting one does not move any real money — it lets a prospective
# investor flag their intent on the Investors page so the Owner knows to
# expect a bank transfer and can match it up once it actually arrives. Once
# the Owner confirms a pledge as paid, a matching entry is added to the
# deposits ledger and the pledge is marked "confirmed" for an audit trail.
# -----------------------------------------------------------------------
PLEDGES_PATH = os.environ.get("INVESTOR_PLEDGES_PATH") or (getattr(config, "INVESTOR_PLEDGES_PATH", None) if config else None) or "/data/investor_pledges.json"

VALID_PLEDGE_STATUSES = frozenset(("pending", "confirmed", "declined"))

_pledges_lock = threading.Lock()


def load_pledges(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load the investor-pledge log. Returns [] if absent/invalid."""
    path = path or PLEDGES_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                logger.warning("deposits: pledges file content not a list, returning empty list")
                return []
            return data
    except Exception as exc:
        logger.exception("deposits: failed to load pledges: %s", exc)
        return []


def save_pledges(pledges: List[Dict[str, Any]], path: Optional[str] = None) -> bool:
    """Persist *pledges* to *path* via an atomic write."""
    path = path or PLEDGES_PATH
    ok = _atomic_write(path, pledges)
    if not ok:
        logger.error("deposits: atomic write of pledges failed")
    return ok


def add_pledge(name: str, amount: Any, note: str = "", path: Optional[str] = None, tier: Optional[str] = None) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Record a new "intent to invest" pledge. Returns (ok, record, status).

    *tier* ("tradingview" or "full") is the investor type chosen by a new
    investor; it is applied once the pledge is confirmed. Ignored if invalid."""
    investor_name = str(name or "").strip()
    if not investor_name or is_backend_investor(investor_name):
        return False, None, "invalid_investor"
    investor_name = investor_name[:100]

    amt = _coerce_amount(amount)
    if amt is None:
        return False, None, "invalid_amount"

    min_amount = float(getattr(config, "PLEDGE_MIN_AMOUNT", 50.0) or 50.0) if config else 50.0
    max_amount = float(getattr(config, "PLEDGE_MAX_AMOUNT", 500.0) or 500.0) if config else 500.0
    if amt < min_amount or amt > max_amount:
        return False, None, "amount_out_of_range"

    record = {
        "id": uuid.uuid4().hex,
        "investor": investor_name,
        "amount": amt,
        "note": (str(note).strip() if note else "")[:500],
        "status": "pending",
        "created_at": _now_iso(),
    }
    chosen_tier = str(tier or "").strip().lower()
    if chosen_tier in VALID_TIERS:
        record["tier"] = chosen_tier

    with _pledges_lock:
        pledges = load_pledges(path)
        pledges.append(record)
        if not save_pledges(pledges, path):
            return False, None, "save_failed"
        return True, record, "created"


def list_pledges_sorted(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return pledges newest-first by ``created_at``."""
    pledges = load_pledges(path)
    return sorted(pledges, key=lambda p: str(p.get("created_at") or ""), reverse=True)


def split_pledges(pledges: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split *pledges* into (pending, completed) — "completed" meaning
    confirmed or declined, i.e. anything the Owner (or auto-detection) has
    already resolved one way or the other."""
    pending = [p for p in pledges if str(p.get("status")) == "pending"]
    completed = [p for p in pledges if str(p.get("status")) != "pending"]
    return pending, completed


def set_pledge_status(
    pledge_id: str,
    status: str,
    path: Optional[str] = None,
    resolved_by: str = "owner",
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Update a pledge's status (e.g. Owner confirming payment arrived).

    *resolved_by* records who/what resolved a non-pending status — "owner"
    for a manual confirm/decline from the dashboard, or "auto" when the
    balance-change detector matched it automatically."""
    target = str(pledge_id or "").strip()
    if not target:
        return False, None, "invalid_id"
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in VALID_PLEDGE_STATUSES:
        return False, None, "invalid_status"

    with _pledges_lock:
        pledges = load_pledges(path)
        for pledge in pledges:
            if str(pledge.get("id")) == target:
                pledge["status"] = normalized_status
                if normalized_status != "pending":
                    pledge["resolved_at"] = _now_iso()
                    pledge["resolved_by"] = resolved_by
                else:
                    pledge.pop("resolved_at", None)
                    pledge.pop("resolved_by", None)
                if not save_pledges(pledges, path):
                    return False, None, "save_failed"
                pledge_tier = str(pledge.get("tier") or "")
                if normalized_status == "confirmed" and pledge_tier in VALID_TIERS and pledge.get("investor"):
                    set_investor_tier(pledge["investor"], pledge_tier)
                return True, pledge, "updated"
        return False, None, "not_found"


def delete_pledge(pledge_id: str, path: Optional[str] = None) -> Tuple[bool, str]:
    """Remove one pledge row by its ``id``."""
    target = str(pledge_id or "").strip()
    if not target:
        return False, "invalid_id"

    with _pledges_lock:
        pledges = load_pledges(path)
        remaining = [p for p in pledges if str(p.get("id")) != target]
        if len(remaining) == len(pledges):
            return False, "not_found"
        if not save_pledges(remaining, path):
            return False, "save_failed"
        return True, "deleted"


# -----------------------------------------------------------------------
# Investor withdrawal requests.
#
# pending -> approved (Owner) -> completed. Completion happens one of two ways:
#   * "broker": the balance-change detector sees funds leave the trading
#     account and matches it to the approved request (units are redeemed);
#   * "owner": no balance change appeared, i.e. the Owner paid the investor
#     personally. The investor's units are then transferred to the Owner at
#     the current NAV (a withdrawal for the investor + an equal deposit for
#     the Owner), so the fund balance and everyone else's share are unchanged.
# -----------------------------------------------------------------------
WITHDRAWALS_PATH = os.environ.get("WITHDRAWAL_REQUESTS_PATH") or (getattr(config, "WITHDRAWAL_REQUESTS_PATH", None) if config else None) or "/data/withdrawal_requests.json"
VALID_WITHDRAWAL_STATUSES = frozenset(("pending", "approved", "completed", "declined"))
_withdrawals_lock = threading.Lock()


def lock_days() -> int:
    try:
        return max(0, int(getattr(config, "INVESTOR_LOCK_DAYS", 30))) if config else 30
    except (TypeError, ValueError):
        return 30


def load_withdrawal_requests(path: Optional[str] = None) -> List[Dict[str, Any]]:
    path = path or WITHDRAWALS_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as exc:
        logger.exception("deposits: failed to load withdrawal requests: %s", exc)
        return []


def save_withdrawal_requests(requests_: List[Dict[str, Any]], path: Optional[str] = None) -> bool:
    ok = _atomic_write(path or WITHDRAWALS_PATH, requests_)
    if not ok:
        logger.error("deposits: atomic write of withdrawal requests failed")
    return ok


def list_withdrawal_requests_sorted(path: Optional[str] = None) -> List[Dict[str, Any]]:
    return sorted(load_withdrawal_requests(path), key=lambda r: str(r.get("created_at") or ""), reverse=True)


def split_withdrawal_requests(requests_: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split into (open: pending/approved, closed: completed/declined)."""
    open_ = [r for r in requests_ if str(r.get("status")) in ("pending", "approved")]
    closed = [r for r in requests_ if str(r.get("status")) not in ("pending", "approved")]
    return open_, closed


def _same_name(a: Any, b: Any) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def canonical_investor_name(name: Any, entries: List[Dict[str, Any]]) -> Optional[str]:
    """Return the ledger spelling of *name*, or None if they have no capital recorded."""
    for known in list_investors(entries):
        if _same_name(known, name):
            return known
    return None


def withdrawable_summary(
    investor: str,
    entries: List[Dict[str, Any]],
    requests_: Optional[List[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Capital an investor may currently request back.

    Each deposit is locked for ``lock_days()`` after it was made. Unlocked
    capital = deposits past their lock less everything already withdrawn,
    minus amounts already tied up in pending/approved requests."""
    now = now or _now_naive_uk()
    days = lock_days()
    unlocked = 0.0
    locked = 0.0
    withdrawn = 0.0
    next_unlock: Optional[datetime] = None
    for e in entries or []:
        if not isinstance(e, dict) or not _same_name(e.get("investor"), investor):
            continue
        try:
            amt = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if e.get("type") == "withdrawal":
            withdrawn += amt
        elif e.get("type") == "deposit":
            when = _parse_entry_datetime(e.get("occurred_at")) or _parse_entry_datetime(e.get("created_at")) or now
            unlock_at = when + timedelta(days=days)
            if unlock_at <= now:
                unlocked += amt
            else:
                locked += amt
                if next_unlock is None or unlock_at < next_unlock:
                    next_unlock = unlock_at
    reserved = sum(
        float(r.get("amount") or 0) for r in (requests_ if requests_ is not None else load_withdrawal_requests())
        if _same_name(r.get("investor"), investor) and str(r.get("status")) in ("pending", "approved")
    )
    available = max(0.0, unlocked - withdrawn - reserved)
    return {
        "available": round(available, 2),
        "locked": round(locked, 2),
        "reserved": round(reserved, 2),
        "next_unlock": next_unlock,
        "lock_days": days,
    }


def add_withdrawal_request(
    investor: str,
    amount: Any,
    note: str = "",
    requested_by: str = "investor",
    enforce_lock: bool = True,
    path: Optional[str] = None,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Record a pending withdrawal request. Returns (ok, record, status)."""
    entries = entries if entries is not None else load_entries(LOG_PATH)
    name = canonical_investor_name(investor, entries)
    owner_name = str(getattr(config, "OWNER_INVESTOR_NAME", "") or "").strip() if config else ""
    if not name or (owner_name and _same_name(name, owner_name)):
        return False, None, "invalid_investor"
    amt = _coerce_amount(amount)
    if amt is None:
        return False, None, "invalid_amount"

    with _withdrawals_lock:
        existing = load_withdrawal_requests(path)
        if enforce_lock:
            summary = withdrawable_summary(name, entries, existing)
            if amt > summary["available"] + 0.005:
                return False, None, "funds_locked" if summary["available"] <= 0 else "amount_exceeds_unlocked"
        record = {
            "id": uuid.uuid4().hex,
            "investor": name,
            "amount": amt,
            "note": (str(note).strip() if note else "")[:500],
            "status": "pending",
            "requested_by": requested_by,
            "created_at": _now_iso(),
        }
        existing.append(record)
        if not save_withdrawal_requests(existing, path):
            return False, None, "save_failed"
        return True, record, "created"


def _update_withdrawal(request_id: str, allowed_from: Tuple[str, ...], changes: Dict[str, Any], path: str):
    target = str(request_id or "").strip()
    with _withdrawals_lock:
        existing = load_withdrawal_requests(path)
        for r in existing:
            if str(r.get("id")) == target:
                if str(r.get("status")) not in allowed_from:
                    return False, None, "invalid_transition"
                r.update(changes)
                if not save_withdrawal_requests(existing, path):
                    return False, None, "save_failed"
                return True, r, "updated"
    return False, None, "not_found"


def approve_withdrawal_request(request_id: str, path: Optional[str] = None):
    return _update_withdrawal(request_id, ("pending",), {"status": "approved", "approved_at": _now_iso()}, path)


def decline_withdrawal_request(request_id: str, path: Optional[str] = None):
    return _update_withdrawal(request_id, ("pending", "approved"), {"status": "declined", "resolved_at": _now_iso(), "resolved_by": "owner"}, path)


def delete_withdrawal_request(request_id: str, path: Optional[str] = None) -> Tuple[bool, str]:
    target = str(request_id or "").strip()
    with _withdrawals_lock:
        existing = load_withdrawal_requests(path)
        remaining = [r for r in existing if str(r.get("id")) != target]
        if len(remaining) == len(existing):
            return False, "not_found"
        return (True, "deleted") if save_withdrawal_requests(remaining, path) else (False, "save_failed")


def _ledger_add(*args, **kwargs):
    """add_entry against the ledger path resolved at call time."""
    return add_entry(*args, path=LOG_PATH, **kwargs)


def complete_withdrawal_request(
    request_id: str,
    method: str,
    resolved_by: str = "owner",
    path: Optional[str] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Complete an *approved* request. ``method`` is "broker" (funds left the
    trading account) or "owner" (Owner paid personally: units move to Owner)."""
    if method not in ("broker", "owner"):
        return False, None, "invalid_method"
    target = str(request_id or "").strip()
    with _withdrawals_lock:
        existing = load_withdrawal_requests(path)
        req = next((r for r in existing if str(r.get("id")) == target), None)
        if req is None:
            return False, None, "not_found"
        if str(req.get("status")) != "approved":
            return False, None, "invalid_transition"
        investor = req.get("investor", "")
        amount = req.get("amount")

        if method == "broker":
            ok, _e, status = _ledger_add(
                "withdrawal", amount, investor,
                f"Approved withdrawal detected as a balance decrease ({resolved_by})",
            )
            if not ok:
                return False, None, status
        else:
            owner_name = str(getattr(config, "OWNER_INVESTOR_NAME", "") or "").strip() if config else ""
            if not owner_name or _same_name(owner_name, investor):
                return False, None, "no_owner"
            ok, w_entry, status = _ledger_add(
                "withdrawal", amount, investor,
                "Approved withdrawal paid by Owner personally: units transferred to Owner at current NAV",
            )
            if not ok:
                return False, None, status
            ok, _e, status = _ledger_add(
                "deposit", amount, owner_name,
                f"Units taken over from {investor} (approved withdrawal paid by Owner)",
            )
            if not ok:
                delete_entry((w_entry or {}).get("id"), LOG_PATH)
                return False, None, status

        req.update({
            "status": "completed",
            "completed_via": method,
            "resolved_at": _now_iso(),
            "resolved_by": resolved_by,
        })
        if not save_withdrawal_requests(existing, path):
            return False, None, "save_failed"
        return True, req, "completed"


def find_matching_approved_withdrawal(amount: float, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Approved request whose amount is closest to *amount* (within
    ``PLEDGE_MATCH_TOLERANCE``), or None."""
    tolerance = float(getattr(config, "PLEDGE_MATCH_TOLERANCE", 2.0) or 2.0) if config else 2.0
    best = None
    for r in load_withdrawal_requests(path):
        if str(r.get("status")) != "approved":
            continue
        try:
            diff = abs(float(r.get("amount")) - amount)
        except (TypeError, ValueError):
            continue
        if diff <= tolerance and (best is None or diff < best[0]):
            best = (diff, r)
    return best[1] if best else None


def settle_stale_approved_withdrawals(now: Optional[datetime] = None, path: Optional[str] = None) -> List[str]:
    """Approved requests with no matching balance change after
    ``WITHDRAWAL_AUTO_SETTLE_DAYS`` are settled via the Owner (units
    transferred). Returns the ids settled."""
    days = float(getattr(config, "WITHDRAWAL_AUTO_SETTLE_DAYS", 3) or 0) if config else 3.0
    if days <= 0:
        return []
    now = now or _now_naive_uk()
    settled = []
    for r in load_withdrawal_requests(path):
        if str(r.get("status")) != "approved":
            continue
        approved_at = _parse_entry_datetime(r.get("approved_at"))
        if approved_at is None or now - approved_at < timedelta(days=days):
            continue
        ok, _rec, _status = complete_withdrawal_request(r.get("id"), "owner", resolved_by="auto", path=path)
        if ok:
            settled.append(r.get("id"))
    return settled


def _now_naive_uk() -> datetime:
    """Return the current time as a naive datetime using UK local wall-clock
    values (GMT/BST), matching the naive values produced by
    ``_parse_entry_datetime``/trade_log's exit timestamps — so comparisons
    between the two are on the same clock, not off by the UTC/UK offset."""
    if UK_TZ:
        try:
            return datetime.now(UK_TZ).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_entry_datetime(raw: Optional[str]) -> Optional[datetime]:
    """Parse an entry's ``occurred_at``/``created_at`` string into a naive
    (UTC-equivalent) datetime. Accepts plain dates ("2026-01-01") and
    ISO-8601 timestamps (with or without a trailing "Z")."""
    if not raw:
        return None
    s = str(raw).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


def investor_breakdown(
    entries: List[Dict[str, Any]],
    trade_pnl_events: Optional[List[Dict[str, Any]]] = None,
    current_balance: Optional[float] = None,
    as_of: Optional[datetime] = None,
    owner_override_pct_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute each investor's ownership and ROI using NAV-per-unit accounting.

    This is the standard mutual-fund allocation model: every deposit buys
    "units" of the fund at its *current* NAV-per-unit; every withdrawal
    redeems units at the current NAV-per-unit. Each closed trade's PnL
    moves the fund's total value, which moves NAV-per-unit for everyone
    holding units *at that moment*.

    The critical property this gives us (unlike a flat/time-weighted split
    of total gain-loss): an investor who deposits today starts holding
    units at today's NAV, so prior gains/losses never retroactively apply
    to them, and future trade PnL only affects them proportional to the
    units they hold at the time each trade closes. Depositing with no
    trades closed since shows ~0 gain/loss, not a share of historical P&L.

    ``trade_pnl_events`` is an optional list of ``{"ts": datetime-or-iso-str,
    "pnl": float}`` dicts, one per closed trade with a resolvable GBP PnL and
    exit time, used to drive the NAV-per-unit timeline. Pass the caller's
    already FX-converted trade PnL series (see dashboard.py).

    Any trade PnL that closed *before* the first capital entry (e.g. the
    bot was already trading before anyone's deposits were tracked) has no
    unit holders to attribute it to, so it is credited directly to the
    configured owner (``config.OWNER_INVESTOR_NAME``) as pre-ledger equity,
    or left unattributed if no owner is configured/present.

    A configured operator allocation (``config.OWNER_INVESTOR_OVERRIDE_PCT``,
    default 15%) is issued to the owner as operator units, covering
    backend/hosting costs. It applies only when NAV-per-unit exceeds its
    previous peak (a high-water mark), and only to the growth above that
    peak, so recovering earlier drawdowns attracts no allocation. Units are
    issued at that moment's NAV, diluting other unit holders only going
    forward, never retroactively.

    ``owner_override_pct_override``, when given, replaces the configured
    percentage for this call only — used by dashboard.py to run a second,
    "Trader-sleeve" simulation (restricted to full-tier investors) with the
    allocation disabled, since that sleeve's profits are split purely pro-rata.
    """
    as_of = as_of or _now_naive_uk()

    owner_name = str(getattr(config, "OWNER_INVESTOR_NAME", "Aleks") or "").strip()
    owner_override_pct = (
        float(owner_override_pct_override)
        if owner_override_pct_override is not None
        else float(getattr(config, "OWNER_INVESTOR_OVERRIDE_PCT", 15) or 0)
    )

    # Build the chronological event timeline: capital flows + trade PnL.
    capital_events = []
    deposited_totals: Dict[str, float] = {}
    withdrawn_totals: Dict[str, float] = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("investor") or "").strip() or "Unassigned"
        amt = e.get("amount") or 0
        try:
            amt = float(amt)
        except (TypeError, ValueError):
            continue
        etype = e.get("type")
        if etype not in ("deposit", "withdrawal"):
            continue

        occurred = _parse_entry_datetime(e.get("occurred_at")) or _parse_entry_datetime(e.get("created_at"))
        backend = is_backend_investor(name)
        capital_events.append({
            "ts": occurred or as_of, "kind": "capital", "ctype": etype,
            "investor": name, "amount": amt, "backend": backend,
            "owner_paid": is_owner_paid_backend(name),
        })

        if backend:
            continue
        if etype == "deposit":
            deposited_totals[name] = deposited_totals.get(name, 0.0) + amt
        else:
            withdrawn_totals[name] = withdrawn_totals.get(name, 0.0) + amt

    pnl_events = []
    for p in trade_pnl_events or []:
        if not isinstance(p, dict):
            continue
        try:
            amount = float(p.get("pnl"))
        except (TypeError, ValueError):
            continue
        raw_ts = p.get("ts")
        ts = raw_ts if isinstance(raw_ts, datetime) else _parse_entry_datetime(raw_ts)
        if ts is None:
            continue
        pnl_events.append({"ts": ts, "kind": "pnl", "amount": amount})

    owner_key = next(
        (name for name in deposited_totals.keys() | withdrawn_totals.keys() if name.strip().lower() == owner_name.lower()),
        None,
    ) if owner_name else None

    first_capital_ts = min((ev["ts"] for ev in capital_events if not ev["backend"]), default=None)

    pre_ledger_pnl = round(
        sum(ev["amount"] for ev in pnl_events if first_capital_ts is None or ev["ts"] < first_capital_ts),
        2,
    )

    timeline = [ev for ev in capital_events if True]
    timeline += [ev for ev in pnl_events if first_capital_ts is not None and ev["ts"] >= first_capital_ts]
    timeline.sort(key=lambda ev: ev["ts"])

    fund_value = 0.0
    total_units = 0.0
    nav_per_unit = 1.0
    # High-water mark: the highest NAV-per-unit reached after any operator
    # allocation. Operator units are only issued on NAV growth above it.
    high_water_mark = 1.0
    investor_units: Dict[str, float] = {}

    for ev in timeline:
        if ev["kind"] == "pnl":
            prior_nav = nav_per_unit
            fund_value += ev["amount"]
            if total_units > 0:
                nav_per_unit = fund_value / total_units
            else:
                # No one holds units right now (e.g. everyone redeemed); this
                # PnL has no owner to attribute to, so reset the baseline for
                # whoever deposits next rather than crash on a divide-by-zero.
                fund_value = 0.0
                nav_per_unit = 1.0
                high_water_mark = 1.0
                continue

            # Operator allocation, paid in newly issued operator units
            # (dilutes other holders going forward only). Only the part of
            # NAV growth above the previous peak (high-water mark) counts.
            if ev["amount"] > 0 and owner_key and 0 < owner_override_pct < 100 and nav_per_unit > 0:
                gain_above_peak = (nav_per_unit - max(high_water_mark, prior_nav)) * total_units
                if gain_above_peak > 0:
                    allocation = gain_above_peak * (owner_override_pct / 100.0)
                    allocation_units = allocation / nav_per_unit
                    investor_units[owner_key] = investor_units.get(owner_key, 0.0) + allocation_units
                    total_units += allocation_units
                    nav_per_unit = fund_value / total_units if total_units > 0 else nav_per_unit
            high_water_mark = max(high_water_mark, nav_per_unit)
        else:  # capital event
            if ev["backend"] and ev.get("owner_paid"):
                # Owner covered the cost personally: shift units from every
                # other holder to the owner; fund value and NAV are unchanged.
                if total_units <= 0 or fund_value <= 0 or not owner_key:
                    continue
                signed = -ev["amount"] if ev["ctype"] == "withdrawal" else ev["amount"]
                moved = 0.0
                for holder in list(investor_units):
                    if holder == owner_key:
                        continue
                    delta = investor_units[holder] * signed / fund_value
                    delta = max(delta, -investor_units[holder]) if signed < 0 else delta
                    investor_units[holder] += delta
                    moved += delta
                investor_units[owner_key] = investor_units.get(owner_key, 0.0) - moved
                continue
            if ev["backend"]:
                # Backend cost: scale every holder's units by the same factor
                # (NAV-per-unit is unchanged), so each investor bears or
                # recovers the same percentage. No-op with no unit holders.
                if total_units <= 0 or fund_value <= 0:
                    continue
                signed = -ev["amount"] if ev["ctype"] == "withdrawal" else ev["amount"]
                factor = max(0.0, (fund_value + signed) / fund_value)
                for holder in investor_units:
                    investor_units[holder] *= factor
                total_units *= factor
                fund_value += signed if factor > 0 else -fund_value
                if total_units > 0:
                    nav_per_unit = fund_value / total_units
                continue
            name = ev["investor"]
            safe_nav = nav_per_unit if nav_per_unit > 0 else 0.000001
            if ev["ctype"] == "deposit":
                units = ev["amount"] / safe_nav
                investor_units[name] = investor_units.get(name, 0.0) + units
                total_units += units
                fund_value += ev["amount"]
            else:  # withdrawal
                units = ev["amount"] / safe_nav
                investor_units[name] = investor_units.get(name, 0.0) - units
                total_units -= units
                fund_value -= ev["amount"]
            if total_units > 0:
                nav_per_unit = fund_value / total_units

    balance = None
    if current_balance is not None:
        try:
            balance = float(current_balance)
        except (TypeError, ValueError):
            balance = None

    # The ledger-simulated fund value (deposits/withdrawals + recorded
    # trade PnL) can drift from the real, live broker balance — e.g. when
    # some closed trades are missing from the trade log, or untracked
    # fees/swaps apply. Left unreconciled, the headline "overall" gain/loss
    # (driven by the real balance) can disagree with the sum of individual
    # investors' allocated gain/loss (driven by the simulation), which is
    # exactly the inconsistency investors would notice and flag. Attribute
    # any such drift to the owner (same treatment as pre-ledger PnL) so the
    # simulated total always reconciles exactly to the real balance.
    tracked_total = fund_value + pre_ledger_pnl
    reconciliation_adjustment = round(balance - tracked_total, 2) if balance is not None and owner_key else 0.0

    # Seed the owner's value with any pre-ledger PnL (trading that happened
    # before anyone's deposits were tracked) plus any live-balance drift.
    owner_seed_value = (pre_ledger_pnl + reconciliation_adjustment) if owner_key else 0.0

    all_names = sorted(deposited_totals.keys() | withdrawn_totals.keys(), key=str.lower)

    total_net_contribution = round(
        sum(deposited_totals.values()) - sum(withdrawn_totals.values()), 2
    )
    overall_gain_loss = round(balance - total_net_contribution, 2) if balance is not None else None
    overall_roi_pct = (
        round((overall_gain_loss / total_net_contribution) * 100, 2)
        if overall_gain_loss is not None and total_net_contribution not in (0, 0.0)
        else None
    )

    investors = []
    for name in all_names:
        units = investor_units.get(name, 0.0)
        share_pct = round((units / total_units) * 100.0, 2) if total_units > 0 else 0.0
        base_value = units * nav_per_unit
        if name == owner_key:
            base_value += owner_seed_value
        current_value = round(base_value, 2)

        deposited = round(deposited_totals.get(name, 0.0), 2)
        withdrawn = round(withdrawn_totals.get(name, 0.0), 2)
        net_contribution = round(deposited - withdrawn, 2)

        allocated_gain_loss = round(current_value - net_contribution, 2)
        roi_pct = (
            round((allocated_gain_loss / net_contribution) * 100, 2)
            if net_contribution not in (0, 0.0)
            else None
        )

        investors.append({
            "investor": name,
            "deposited": deposited,
            "withdrawn": withdrawn,
            "net_contribution": net_contribution,
            "units_held": round(units, 4),
            "share_pct": share_pct,
            "is_owner": name == owner_key,
            "allocated_gain_loss": allocated_gain_loss,
            "current_value": current_value,
            "roi_pct": roi_pct,
        })

    return {
        "investors": investors,
        "total_net_contribution": total_net_contribution,
        "current_balance": balance,
        "overall_gain_loss": overall_gain_loss,
        "overall_roi_pct": overall_roi_pct,
        "owner_name": owner_key,
        "owner_override_pct": owner_override_pct if owner_key is not None else None,
        "pre_ledger_pnl": pre_ledger_pnl if owner_key else None,
        "reconciliation_adjustment": reconciliation_adjustment if owner_key else None,
        # Simulated total (fund_value + pre-ledger PnL), exposed so callers
        # running multiple parallel sleeves (e.g. TradingView + Trader) can
        # combine them and reconcile against the real balance themselves.
        "tracked_total": round(tracked_total, 2),
    }


# -----------------------------------------------------------------------
# Automatic balance-change detection.
#
# On each dashboard refresh the live broker balance is compared against
# what closed-trade PnL alone would explain since the last check. If the
# balance moved abruptly with no matching trades/fees (e.g. 1500 -> 1600
# with nothing closed in between), that's money the Owner moved in/out of
# the account outside the app, so it's recorded automatically in the
# Deposits ledger — matched to a pending pledge's investor when the amount
# lines up, otherwise logged as "Unassigned" for the Owner to reassign.
# -----------------------------------------------------------------------
BALANCE_STATE_PATH = os.environ.get("BALANCE_STATE_PATH") or (getattr(config, "BALANCE_STATE_PATH", None) if config else None) or "/data/balance_state.json"

_balance_lock = threading.Lock()
_last_balance_check_monotonic = 0.0


def _load_balance_state(path: str = BALANCE_STATE_PATH) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "balance" in data:
                return data
    except Exception as exc:
        logger.exception("deposits: failed to load balance state: %s", exc)
    return None


def _save_balance_state(balance: float, as_of: str, path: str = BALANCE_STATE_PATH) -> bool:
    ok = _atomic_write(path, {"balance": round(balance, 2), "as_of": as_of})
    if not ok:
        logger.error("deposits: atomic write of balance state failed")
    return ok


def _find_matching_pending_pledge(amount: float, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return the pending pledge whose amount is closest to *amount*,
    within ``config.PLEDGE_MATCH_TOLERANCE`` GBP, or ``None``."""
    tolerance = float(getattr(config, "PLEDGE_MATCH_TOLERANCE", 2.0) or 2.0) if config else 2.0
    candidates = []
    for p in load_pledges(path):
        if str(p.get("status")) != "pending":
            continue
        try:
            pledge_amount = float(p.get("amount"))
        except (TypeError, ValueError):
            continue
        diff = abs(pledge_amount - amount)
        if diff <= tolerance:
            candidates.append((diff, p))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def detect_and_record_balance_change(
    current_balance: Optional[float],
    trade_pnl_events: Optional[List[Dict[str, Any]]] = None,
    min_interval_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Compare *current_balance* against the last observed balance plus any
    closed-trade PnL since then. Record an unexplained delta as a deposit
    (balance rose) or withdrawal (balance fell) — auto-confirming a
    matching pending pledge when one exists.

    ``trade_pnl_events`` should be the full (not per-sleeve) closed-trade
    PnL series in GBP, e.g. ``dashboard._trade_pnl_events(combined_trades)``.

    Cheap to call on every dashboard refresh: throttled to at most once per
    ``min_interval_seconds`` (default ``config.BALANCE_AUTO_DETECT_MIN_INTERVAL_SECONDS``)
    and a no-op once the balance is already reconciled.
    """
    global _last_balance_check_monotonic

    if current_balance is None:
        return {"action": "skipped", "reason": "no_balance"}
    try:
        current_balance = float(current_balance)
    except (TypeError, ValueError):
        return {"action": "skipped", "reason": "invalid_balance"}

    interval = (
        min_interval_seconds
        if min_interval_seconds is not None
        else float(getattr(config, "BALANCE_AUTO_DETECT_MIN_INTERVAL_SECONDS", 15) or 15) if config else 15.0
    )

    with _balance_lock:
        now_monotonic = time.monotonic()
        if _last_balance_check_monotonic and (now_monotonic - _last_balance_check_monotonic) < interval:
            return {"action": "throttled"}
        _last_balance_check_monotonic = now_monotonic

        state = _load_balance_state()
        now_iso = _now_iso()
        if state is None:
            # First-ever observation: just establish the baseline, nothing to detect yet.
            _save_balance_state(current_balance, now_iso)
            return {"action": "baseline_set", "balance": current_balance}

        try:
            last_balance = float(state.get("balance"))
        except (TypeError, ValueError):
            last_balance = current_balance
        last_as_of = _parse_entry_datetime(state.get("as_of")) or _now_naive_uk()

        pnl_since = sum(
            ev.get("pnl", 0.0) for ev in (trade_pnl_events or [])
            if isinstance(ev.get("ts"), datetime) and ev["ts"] >= last_as_of
        )
        expected_balance = last_balance + pnl_since
        delta = round(current_balance - expected_balance, 2)

        tolerance = float(getattr(config, "BALANCE_AUTO_DETECT_TOLERANCE", 1.0) or 1.0) if config else 1.0
        if abs(delta) <= tolerance:
            _save_balance_state(current_balance, now_iso)
            return {"action": "noop", "delta": delta}

        result: Dict[str, Any] = {"delta": delta}

        if delta > 0:
            deposit_min = float(getattr(config, "PLEDGE_MIN_AMOUNT", 50.0) or 50.0) if config else 50.0
            if delta < deposit_min:
                # Too small to be a confident deposit signal (e.g. rounding,
                # interest) — leave the baseline as-is so it keeps
                # accumulating across checks, same noise filter as the
                # withdrawal side below, rather than cluttering Deposits.
                return {"action": "below_deposit_threshold", "delta": delta}
            matched = _find_matching_pending_pledge(delta)
            if matched:
                ok, _entry, status = add_entry(
                    "deposit",
                    matched.get("amount"),
                    matched.get("investor", ""),
                    f"Auto-confirmed: balance increase of £{delta:.2f} matched pending pledge ({matched.get('note') or 'no note'})",
                )
                if ok:
                    set_pledge_status(matched.get("id"), "confirmed", resolved_by="auto")
                    result.update({"action": "pledge_matched", "pledge_id": matched.get("id"), "investor": matched.get("investor")})
                else:
                    result.update({"action": "failed", "status": status})
            else:
                ok, _entry, status = add_entry(
                    "deposit",
                    delta,
                    "Unassigned",
                    "Auto-detected balance increase (no matching trade or pledge found)",
                )
                result["action"] = "auto_deposit" if ok else "failed"
                if not ok:
                    result["status"] = status
        else:
            matched_withdrawal = find_matching_approved_withdrawal(abs(delta))
            if matched_withdrawal:
                ok, _rec, status = complete_withdrawal_request(matched_withdrawal.get("id"), "broker", resolved_by="auto")
                result.update({
                    "action": "withdrawal_matched" if ok else "failed",
                    "request_id": matched_withdrawal.get("id"),
                    "investor": matched_withdrawal.get("investor"),
                })
                if not ok:
                    result["status"] = status
                _save_balance_state(current_balance, now_iso)
                logger.info("deposits: balance auto-detection result: %s", result)
                return result
            withdrawal_min = float(getattr(config, "BALANCE_AUTO_DETECT_WITHDRAWAL_MIN", 100.0) or 100.0) if config else 100.0
            if abs(delta) < withdrawal_min:
                # Too small to be a real withdrawal (e.g. overnight/swap
                # fees) — leave the baseline as-is so this drift keeps
                # accumulating across checks until it's either explained by
                # future trade PnL or grows past the threshold, rather than
                # cluttering the Deposits ledger with noise.
                return {"action": "below_withdrawal_threshold", "delta": delta}
            ok, _entry, status = add_entry(
                "withdrawal",
                abs(delta),
                "Unassigned",
                "Auto-detected balance decrease (no matching trade found)",
            )
            result["action"] = "auto_withdrawal" if ok else "failed"
            if not ok:
                result["status"] = status

        _save_balance_state(current_balance, now_iso)
        logger.info("deposits: balance auto-detection result: %s", result)
        return result


# -----------------------------------------------------------------------
# Overnight adjustment fees. The broker takes these out of the account
# between 21:00 and 21:05 UK time (occasionally 22:00-22:05, so the catchment
# is widened to 20:30-22:30). The scheduler snapshots the balance at
# the start of that window and, once it closes, records any small drop not
# explained by closed trades as a Broker Fee (editable on the Transactions
# page if the recorded value is wrong).
# -----------------------------------------------------------------------
OVERNIGHT_FEE_STATE_PATH = os.environ.get("OVERNIGHT_FEE_STATE_PATH") or (getattr(config, "OVERNIGHT_FEE_STATE_PATH", None) if config else None) or "/data/overnight_fee_state.json"
OVERNIGHT_FEE_START = (20, 30)
OVERNIGHT_FEE_END = (22, 30)
OVERNIGHT_FEE_GRACE_MINUTES = 5
OVERNIGHT_ADJUSTMENT_CATEGORY = "overnight_funding_adjustment"


def _fallback_overnight_baseline(window_start: datetime) -> Optional[Tuple[float, datetime]]:
    """Best available pre-window balance when the 20:30 snapshot is missing:
    the last auto-detector balance, else today's first daily snapshot, but only
    if observed before the window opened."""
    state = _load_balance_state(BALANCE_STATE_PATH)
    if state is not None:
        as_of = _parse_entry_datetime(state.get("as_of"))
        try:
            if as_of is not None and as_of < window_start:
                return round(float(state["balance"]), 2), as_of
        except (TypeError, ValueError):
            pass
    today = window_start.date().isoformat()
    for r in _load_balance_history(BALANCE_HISTORY_PATH):
        if r.get("date") == today:
            rec = _parse_entry_datetime(r.get("recorded_at"))
            try:
                if rec is not None and rec < window_start:
                    return round(float(r["balance"]), 2), rec
            except (TypeError, ValueError):
                pass
    return None


def _external_flows_since(since: datetime, until: datetime) -> float:
    """Net signed deposit(+)/withdrawal(-) cash moved on the broker account in
    [since, until], from ledger rows (owner-paid rows never touch the
    broker balance, so they're skipped). A row counts if either its
    occurred_at or the time it was recorded falls in the window."""
    total = 0.0
    for e in load_entries(LOG_PATH):
        if not isinstance(e, dict) or e.get("type") not in ("deposit", "withdrawal"):
            continue
        if is_owner_paid_backend(e.get("investor")):
            continue
        in_window = False
        for key in ("occurred_at", "created_at"):
            ts = _parse_entry_datetime(e.get(key))
            if ts is not None and since <= ts <= until:
                in_window = True
        if not in_window:
            continue
        try:
            amt = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        total += amt if e.get("type") == "deposit" else -amt
    return total


def check_overnight_fee(
    current_balance: Optional[float],
    trade_pnl_events: Optional[List[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    state_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Snapshot the balance at 20:30 UK and, from 22:30, record the unexplained
    small decrease as a Broker Fee. Safe to call frequently; each UK day is
    recorded at most once."""
    state_path = state_path or OVERNIGHT_FEE_STATE_PATH
    if current_balance is None:
        return {"action": "skipped", "reason": "no_balance"}
    try:
        current_balance = float(current_balance)
    except (TypeError, ValueError):
        return {"action": "skipped", "reason": "invalid_balance"}

    now = now or _now_naive_uk()
    start = now.replace(hour=OVERNIGHT_FEE_START[0], minute=OVERNIGHT_FEE_START[1], second=0, microsecond=0)
    end = now.replace(hour=OVERNIGHT_FEE_END[0], minute=OVERNIGHT_FEE_END[1], second=0, microsecond=0)
    if now < start or now >= end + timedelta(minutes=OVERNIGHT_FEE_GRACE_MINUTES):
        return {"action": "skipped", "reason": "outside_window"}

    today = now.date().isoformat()
    with _balance_lock:
        state = None
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception:
                state = None
        if not isinstance(state, dict) or state.get("date") != today:
            state = None

        if state is None:
            if now >= end:
                # The 20:30 balance was never captured (e.g. app was down).
                # Fall back to the last balance observed before the window
                # so the fee is still derived from the unexplained change.
                fallback = _fallback_overnight_baseline(start)
                if fallback is None:
                    return {"action": "skipped", "reason": "no_baseline"}
                state = {
                    "date": today, "baseline": fallback[0],
                    "started_at": fallback[1].isoformat(), "recorded": False,
                }
            else:
                _atomic_write(state_path, {
                    "date": today, "baseline": round(current_balance, 2),
                    "started_at": now.isoformat(), "recorded": False,
                })
                return {"action": "baseline_set", "balance": current_balance}

        if state.get("recorded") or now < end:
            return {"action": "waiting"}

        started_at = _parse_entry_datetime(state.get("started_at")) or start
        pnl_since = sum(
            ev.get("pnl", 0.0) for ev in (trade_pnl_events or [])
            if isinstance(ev.get("ts"), datetime) and ev["ts"] >= started_at
        )
        # Deposits/withdrawals logged in the window also move the balance;
        # exclude them so only the funding adjustment remains.
        flows = _external_flows_since(started_at, now)
        delta = round(current_balance - float(state.get("baseline")) - pnl_since - flows, 2)
        state["recorded"] = True
        _atomic_write(state_path, state)

        max_fee = float(getattr(config, "OVERNIGHT_FEE_MAX", 25.0) or 25.0) if config else 25.0
        if abs(delta) > max_fee or abs(delta) < 0.01:
            return {"action": "no_fee", "delta": delta}

    # Net overnight funding adjustment: charge when the balance fell, credit when it rose.
    fee = round(-delta, 2)
    ok, entry, status = add_entry(
        "fee", fee, "", "Auto-detected overnight funding adjustment (20:30-22:30 UK) - edit if incorrect",
        occurred_at=now.isoformat(), path=LOG_PATH, category=OVERNIGHT_ADJUSTMENT_CATEGORY,
    )
    if not ok:
        return {"action": "failed", "status": status}

    # The fee is now explained, so shift the balance baseline used by the
    # deposit/withdrawal auto-detector to avoid it flagging the same drop.
    with _balance_lock:
        last = _load_balance_state()
        if last is not None:
            try:
                _save_balance_state(float(last["balance"]) - fee, str(last.get("as_of") or _now_iso()))
            except (TypeError, ValueError):
                pass
    logger.info("deposits: recorded overnight fee £%.2f", fee)
    return {"action": "fee_recorded", "amount": fee, "entry_id": entry.get("id")}


# -----------------------------------------------------------------------
# Calendar-aligned balance history for the Dashboard's Daily/Weekly/Monthly
# Return metrics.
#
# These are deliberately *not* rolling windows (e.g. "last 7 days"): Daily
# covers 00:00-23:59 UK time for the current calendar day, Weekly runs from
# this week's Monday, and Monthly runs from the 1st of the current month -
# each measured against the account Balance recorded at that period's
# start. A lightweight one-row-per-UK-calendar-day snapshot (the first
# balance observed that day) is the practical stand-in for "the balance at
# exactly 00:00", since nothing else in the app polls the broker on a timer
# independent of dashboard/webhook activity.
# -----------------------------------------------------------------------
BALANCE_HISTORY_PATH = os.environ.get("BALANCE_HISTORY_PATH") or (getattr(config, "BALANCE_HISTORY_PATH", None) if config else None) or "/data/balance_history.json"
_balance_history_lock = threading.Lock()
# Keep the history file small indefinitely; a year of daily snapshots is
# more than enough for any Monthly Return lookup.
BALANCE_HISTORY_MAX_DAYS = 400


def _load_balance_history(path: str = BALANCE_HISTORY_PATH) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict) and r.get("date")]
    except Exception:
        logger.exception("deposits: failed to load balance history")
    return []


def _save_balance_history(history: List[Dict[str, Any]], path: str = BALANCE_HISTORY_PATH) -> bool:
    ok = _atomic_write(path, history)
    if not ok:
        logger.error("deposits: atomic write of balance history failed")
    return ok


def record_daily_balance_snapshot(current_balance: Optional[float], path: str = BALANCE_HISTORY_PATH) -> None:
    """Record the first observed broker Balance of today (UK calendar date).

    Idempotent/cheap: a no-op once today's snapshot already exists. Safe to
    call on every dashboard refresh.
    """
    if current_balance is None:
        return
    try:
        current_balance = float(current_balance)
    except (TypeError, ValueError):
        return

    today = _now_naive_uk().date().isoformat()
    with _balance_history_lock:
        history = _load_balance_history(path)
        if any(r.get("date") == today for r in history):
            return
        history.append({"date": today, "balance": round(current_balance, 2), "recorded_at": _now_iso()})
        history.sort(key=lambda r: r.get("date") or "")
        if len(history) > BALANCE_HISTORY_MAX_DAYS:
            history = history[-BALANCE_HISTORY_MAX_DAYS:]
        _save_balance_history(history, path)


def _period_boundary_date(now: datetime, period: str):
    """Return the calendar date (a ``date``) marking the start of *period*
    ("daily", "weekly", or "monthly") containing *now*."""
    today = now.date()
    if period == "weekly":
        from datetime import timedelta as _timedelta
        return today - _timedelta(days=today.weekday())  # Monday
    if period == "monthly":
        return today.replace(day=1)
    return today


def _opening_row_on_or_after(history: List[Dict[str, Any]], boundary_date) -> Optional[Dict[str, Any]]:
    boundary_s = boundary_date.isoformat()
    candidates = [r for r in history if (r.get("date") or "") >= boundary_s]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.get("date") or "")


def _row_start(row: Dict[str, Any]) -> Optional[datetime]:
    """When a balance snapshot was taken (falls back to its date's midnight)."""
    return _parse_entry_datetime(row.get("recorded_at")) or _parse_entry_datetime(row.get("date"))


def _trade_pnl_between(
    pnl_events: Optional[List[Dict[str, Any]]],
    start: Optional[datetime],
    end: Optional[datetime] = None,
) -> float:
    """Sum closed-trade PnL (GBP) with exit time in [start, end).

    Calendar returns are measured from trade profitability, not raw balance
    changes, so deposits, withdrawals (including Backend ones) and any other
    ledger bookkeeping can never move them."""
    total = 0.0
    for ev in pnl_events or []:
        if not isinstance(ev, dict):
            continue
        ts = ev.get("ts")
        if not isinstance(ts, datetime):
            ts = _parse_entry_datetime(ts)
        if ts is None or (start is not None and ts < start) or (end is not None and ts >= end):
            continue
        try:
            total += float(ev.get("pnl") or 0)
        except (TypeError, ValueError):
            continue
    return total

def _opening_balance_on_or_after(history: List[Dict[str, Any]], boundary_date) -> Optional[float]:
    """Return the earliest recorded balance on/after *boundary_date*.

    Falling back to the earliest snapshot within the period (rather than
    requiring an exact boundary-date match) means a period that started
    before the bot began tracking balance history still gets a usable
    opening balance instead of no Return figure at all.
    """
    row = _opening_row_on_or_after(history, boundary_date)
    if row is None:
        return None
    try:
        return float(row.get("balance"))
    except (TypeError, ValueError):
        return None


def period_labels(now: Optional[datetime] = None) -> Dict[str, str]:
    """Human-readable labels for the current calendar Daily/Weekly/Monthly
    return periods (UK time): today's date, Mon-Sun range, and month name."""
    from datetime import timedelta as _timedelta
    now = now or _now_naive_uk()
    today = now.date()
    monday = today - _timedelta(days=today.weekday())
    sunday = monday + _timedelta(days=6)
    return {
        "daily": today.strftime("%d/%m/%Y"),
        "weekly": f"{monday.strftime('%d/%m/%y')} - {sunday.strftime('%d/%m/%y')}",
        "weekly_trading": f"{monday.strftime('%d/%m/%y')} - {(monday + _timedelta(days=4)).strftime('%d/%m/%y')}",
        "monthly": today.strftime("%B"),
    }


def compute_period_returns(
    current_balance: Optional[float],
    path: str = BALANCE_HISTORY_PATH,
    pnl_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Optional[float]]:
    """Return calendar-aligned {daily, weekly, monthly} % returns.

    Each is ``closed-trade PnL since the period's opening snapshot /
    opening_balance * 100``, where *opening_balance* is the Balance recorded
    at that period's start (today / this Monday / the 1st of this month), so
    returns reflect trade profitability only (no trades = 0%, regardless of
    deposits/withdrawals). A period with no
    recorded opening balance yet returns ``None`` for both the percentage
    and the opening balance, rather than a misleading 0%.
    """
    result: Dict[str, Optional[float]] = {
        "daily": None, "weekly": None, "monthly": None,
        "daily_opening": None, "weekly_opening": None, "monthly_opening": None,
    }
    if current_balance is None:
        return result
    try:
        current_balance = float(current_balance)
    except (TypeError, ValueError):
        return result

    history = _load_balance_history(path)
    if not history:
        return result

    now = _now_naive_uk()
    for period in ("daily", "weekly", "monthly"):
        boundary = _period_boundary_date(now, period)
        row = _opening_row_on_or_after(history, boundary)
        if row is None:
            continue
        try:
            opening = float(row.get("balance"))
        except (TypeError, ValueError):
            continue
        result[f"{period}_opening"] = round(opening, 2)
        if opening > 0:
            result[period] = round(_trade_pnl_between(pnl_events, _row_start(row)) / opening * 100, 2)
    return result


# Weekly Return goal: 5% is the first milestone; once reached the next goal
# steps up by the same increment (10%, 15%, ...), gamifying the target
# rather than leaving it fixed once hit.
RETURN_GOAL_STEP_PCT = 5.0


def compute_weekly_return_goal_progress(weekly_return: Optional[float], step_pct: float = RETURN_GOAL_STEP_PCT) -> Dict[str, Optional[float]]:
    """Return the next Weekly Return milestone and progress toward it.

    The first goal is *step_pct* (default 5%). Once the current weekly
    return reaches/exceeds a milestone, the next goal becomes the next
    multiple of *step_pct* above it (10%, 15%, ...). Progress is clamped to
    [0, 100] so a return at or past the current milestone still shows a
    full bar rather than overflowing while the next goal is computed.
    """
    if weekly_return is None:
        return {"goal_pct": step_pct, "progress_pct": 0.0}
    goal = (math.floor(weekly_return / step_pct) + 1) * step_pct
    if goal <= 0:
        goal = step_pct
    progress = max(0.0, weekly_return) / goal * 100
    return {"goal_pct": round(goal, 2), "progress_pct": round(min(progress, 100.0), 1)}


# -----------------------------------------------------------------------
# Successful Weeks: a trading week runs Monday -> Friday 21:00 UK (end of
# trading hours). A week is "successful" when its return reaches the 5%
# weekly goal. Week closes are frozen the first time the app observes the
# account after Friday 21:00 so later deposits/withdrawals can't alter them.
# -----------------------------------------------------------------------
WEEKLY_CLOSES_PATH = os.environ.get("WEEKLY_CLOSES_PATH") or (getattr(config, "WEEKLY_CLOSES_PATH", None) if config else None) or "/data/weekly_closes.json"
_weekly_closes_lock = threading.Lock()
WEEK_CLOSE_WEEKDAY = 4  # Friday
WEEK_CLOSE_HOUR = 21
SUCCESSFUL_WEEK_PCT = RETURN_GOAL_STEP_PCT
TRADING_YEAR_WEEKS = 52


def _load_weekly_closes(path: str = WEEKLY_CLOSES_PATH) -> Dict[str, float]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items()}
    except Exception:
        logger.exception("deposits: failed to load weekly closes")
    return {}


def _week_close_passed(now: datetime) -> bool:
    """True once *now* (naive UK) is at/after this week's Friday 21:00."""
    if now.weekday() > WEEK_CLOSE_WEEKDAY:
        return True
    return now.weekday() == WEEK_CLOSE_WEEKDAY and now.hour >= WEEK_CLOSE_HOUR


def record_weekly_close(current_balance: Optional[float], path: str = WEEKLY_CLOSES_PATH, now: Optional[datetime] = None) -> None:
    """Freeze this week's closing balance once Friday 21:00 UK has passed.
    Idempotent: a no-op if already recorded or the close hasn't happened."""
    if current_balance is None:
        return
    try:
        current_balance = float(current_balance)
    except (TypeError, ValueError):
        return
    now = now or _now_naive_uk()
    if not _week_close_passed(now):
        return
    from datetime import timedelta as _timedelta
    week_start = (now.date() - _timedelta(days=now.weekday())).isoformat()
    with _weekly_closes_lock:
        closes = _load_weekly_closes(path)
        if week_start in closes:
            return
        closes[week_start] = round(current_balance, 2)
        _atomic_write(path, closes)


def _completed_week_returns(
    path: str = BALANCE_HISTORY_PATH,
    closes_path: str = WEEKLY_CLOSES_PATH,
    now: Optional[datetime] = None,
    pnl_events: Optional[List[Dict[str, Any]]] = None,
) -> List[Tuple[str, float]]:
    """Return [(week_start_iso, return_pct)] for every completed week, oldest
    first. A week is complete once its Friday 21:00 close is frozen or a later
    week has started; each return is closed-trade PnL / the week's opening
    balance."""
    from datetime import timedelta as _timedelta
    now = now or _now_naive_uk()
    history = sorted(_load_balance_history(path), key=lambda r: r.get("date") or "")
    closes = _load_weekly_closes(closes_path)
    by_week: Dict[str, List[Dict[str, Any]]] = {}
    for r in history:
        try:
            d = datetime.strptime(r.get("date") or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        ws = (d - _timedelta(days=d.weekday())).isoformat()
        by_week.setdefault(ws, []).append(r)

    current_ws = (now.date() - _timedelta(days=now.weekday())).isoformat()
    weeks = sorted(by_week)
    returns: List[Tuple[str, float]] = []
    for ws in weeks:
        if ws > current_ws or (ws == current_ws and ws not in closes):
            continue
        try:
            opening = float(by_week[ws][0].get("balance"))
        except (TypeError, ValueError):
            continue
        closing = closes.get(ws)
        window_end = None
        if closing is None:
            later = [w for w in weeks if w > ws]
            if not later:
                continue
            try:
                closing = float(by_week[later[0]][0].get("balance"))
            except (TypeError, ValueError):
                continue
            window_end = _row_start(by_week[later[0]][0])
        else:
            week_monday = datetime.strptime(ws, "%Y-%m-%d")
            window_end = week_monday + _timedelta(days=WEEK_CLOSE_WEEKDAY, hours=WEEK_CLOSE_HOUR)
        if opening <= 0:
            continue
        week_pnl = _trade_pnl_between(pnl_events, _row_start(by_week[ws][0]), window_end)
        returns.append((ws, week_pnl / opening * 100))
    return returns


def compute_successful_weeks(
    path: str = BALANCE_HISTORY_PATH,
    closes_path: str = WEEKLY_CLOSES_PATH,
    now: Optional[datetime] = None,
    pnl_events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return {"successful_weeks", "total_weeks", "avg_weekly_return"}.

    Each completed week's return is (close - open) / open, where open is the
    first balance snapshot of that week and close is the frozen Friday 21:00
    balance (falling back to the next week's first snapshot). Only the last
    52 weeks count (the trading year). avg_weekly_return is the mean return
    across those weeks, or None when there are none.
    """
    weekly = _completed_week_returns(path, closes_path, now, pnl_events)
    returns = [r for _, r in weekly][-TRADING_YEAR_WEEKS:]
    return {
        "successful_weeks": sum(1 for r in returns if r >= SUCCESSFUL_WEEK_PCT),
        "total_weeks": len(returns),
        "avg_weekly_return": round(sum(returns) / len(returns), 2) if returns else None,
    }


def compute_last_completed_week(
    path: str = BALANCE_HISTORY_PATH,
    closes_path: str = WEEKLY_CLOSES_PATH,
    now: Optional[datetime] = None,
    pnl_events: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Return {"label", "return_pct"} for the most recent completed week."""
    from datetime import timedelta as _timedelta
    weekly = _completed_week_returns(path, closes_path, now, pnl_events)
    if not weekly:
        return None
    ws, ret = weekly[-1]
    monday = datetime.strptime(ws, "%Y-%m-%d").date()
    friday = monday + _timedelta(days=4)
    return {
        "label": f"{monday.strftime('%d/%m/%y')} - {friday.strftime('%d/%m/%y')}",
        "return_pct": round(ret, 2),
    }


def compute_last_completed_month(
    path: str = BALANCE_HISTORY_PATH,
    now: Optional[datetime] = None,
    pnl_events: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Return {"label", "return_pct"} for the previous calendar month, measured
    like the weekly figures (closed-trade PnL / the month's opening balance).
    None when no balance was recorded during that month."""
    from datetime import timedelta as _timedelta
    now = now or _now_naive_uk()
    this_month = now.date().replace(day=1)
    prev_month = (this_month - _timedelta(days=1)).replace(day=1)
    row = _opening_row_on_or_after(_load_balance_history(path), prev_month)
    if row is None or (row.get("date") or "") >= this_month.isoformat():
        return None
    try:
        opening = float(row.get("balance"))
    except (TypeError, ValueError):
        return None
    if opening <= 0:
        return None
    pnl = _trade_pnl_between(pnl_events, _row_start(row), datetime.combine(this_month, datetime.min.time()))
    return {"label": prev_month.strftime("%B %Y"), "return_pct": round(pnl / opening * 100, 2)}
