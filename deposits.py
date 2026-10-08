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
import time
import uuid
from datetime import datetime
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
# reduce the fund as a whole. Entries of type "fee" default to this label
# when no investor is supplied.
FEE_ENTRY_LABEL = "Fund"

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


def load_tiers(path: str = TIERS_PATH) -> Dict[str, str]:
    """Load the investor-tier map (lowercased investor name -> tier). Returns {} if absent/invalid."""
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


def save_tiers(tiers: Dict[str, str], path: str = TIERS_PATH) -> bool:
    """Persist *tiers* to *path* via an atomic write."""
    ok = _atomic_write(path, tiers)
    if not ok:
        logger.error("deposits: atomic write of tiers failed")
    return ok


def get_investor_tier(name: str, tiers: Optional[Dict[str, str]] = None, path: str = TIERS_PATH) -> str:
    """Return the stored tier for *name* ("tradingview" or "full"), defaulting
    to "tradingview" when unset/invalid. Lookup is case-insensitive."""
    key = str(name or "").strip().lower()
    if not key:
        return DEFAULT_TIER
    tiers = tiers if tiers is not None else load_tiers(path)
    tier = tiers.get(key)
    return tier if tier in VALID_TIERS else DEFAULT_TIER


def set_investor_tier(name: str, tier: str, path: str = TIERS_PATH) -> Tuple[bool, str]:
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
    investor: str = "",
    note: str = "",
    occurred_at: Optional[str] = None,
    path: str = LOG_PATH,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Append a new deposit/withdrawal/fee row to the ledger.

    *amount* must be a positive number; direction is carried by *entry_type*
    ("deposit", "withdrawal", or "fee"), not by the sign of the amount.
    *investor* identifies whose capital this entry belongs to, so
    ROI/ownership share can be calculated per-person. Fee entries are a
    broker-level cost (e.g. Capital.com commission/financing charges)
    rather than one investor's capital, so *investor* may be left blank
    for them and defaults to ``FEE_ENTRY_LABEL``.
    """
    normalized_type = str(entry_type or "").strip().lower()
    if normalized_type not in VALID_TYPES:
        return False, None, "invalid_type"

    amt = _coerce_amount(amount)
    if amt is None:
        return False, None, "invalid_amount"

    investor_name = str(investor or "").strip()
    if not investor_name:
        if normalized_type == "fee":
            investor_name = FEE_ENTRY_LABEL
        else:
            return False, None, "invalid_investor"
    investor_name = investor_name[:100]

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
    """Compute totals: total deposited, total withdrawn, total fees, and net contribution."""
    total_deposits = 0.0
    total_withdrawals = 0.0
    total_fees = 0.0
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
        elif e.get("type") == "fee":
            total_fees += amt
    return {
        "total_deposits": round(total_deposits, 2),
        "total_withdrawals": round(total_withdrawals, 2),
        "total_fees": round(total_fees, 2),
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
    return sorted(names, key=str.lower)


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


def load_pledges(path: str = PLEDGES_PATH) -> List[Dict[str, Any]]:
    """Load the investor-pledge log. Returns [] if absent/invalid."""
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


def save_pledges(pledges: List[Dict[str, Any]], path: str = PLEDGES_PATH) -> bool:
    """Persist *pledges* to *path* via an atomic write."""
    ok = _atomic_write(path, pledges)
    if not ok:
        logger.error("deposits: atomic write of pledges failed")
    return ok


def add_pledge(name: str, amount: Any, note: str = "", path: str = PLEDGES_PATH) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Record a new "intent to invest" pledge. Returns (ok, record, status)."""
    investor_name = str(name or "").strip()
    if not investor_name:
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

    with _pledges_lock:
        pledges = load_pledges(path)
        pledges.append(record)
        if not save_pledges(pledges, path):
            return False, None, "save_failed"
        return True, record, "created"


def list_pledges_sorted(path: str = PLEDGES_PATH) -> List[Dict[str, Any]]:
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
    path: str = PLEDGES_PATH,
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
                return True, pledge, "updated"
        return False, None, "not_found"


def delete_pledge(pledge_id: str, path: str = PLEDGES_PATH) -> Tuple[bool, str]:
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
    return datetime.utcnow()


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

    A configured owner performance fee (``config.OWNER_INVESTOR_OVERRIDE_PCT``,
    default 15%) is skimmed from each *profitable* closed trade while other
    investors hold units, and credited to the owner as additional units at
    that moment's NAV (covering backend/hosting costs) — this dilutes other
    unit holders only going forward, never retroactively.

    ``owner_override_pct_override``, when given, replaces the configured
    percentage for this call only — used by dashboard.py to run a second,
    "Trader-sleeve" simulation (restricted to full-tier investors) with the
    fee disabled, since that sleeve's profits are split purely pro-rata.
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
        capital_events.append({"ts": occurred or as_of, "kind": "capital", "ctype": etype, "investor": name, "amount": amt})

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

    first_capital_ts = min((ev["ts"] for ev in capital_events), default=None)

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
    investor_units: Dict[str, float] = {}

    for ev in timeline:
        if ev["kind"] == "pnl":
            fund_value += ev["amount"]
            if total_units > 0:
                nav_per_unit = fund_value / total_units
            else:
                # No one holds units right now (e.g. everyone redeemed); this
                # PnL has no owner to attribute to, so reset the baseline for
                # whoever deposits next rather than crash on a divide-by-zero.
                fund_value = 0.0
                nav_per_unit = 1.0
                continue

            # Owner performance fee on profitable trades, paid in newly
            # issued owner units (dilutes other holders going forward only).
            if ev["amount"] > 0 and owner_key and 0 < owner_override_pct < 100 and nav_per_unit > 0:
                fee = ev["amount"] * (owner_override_pct / 100.0)
                fee_units = fee / nav_per_unit
                investor_units[owner_key] = investor_units.get(owner_key, 0.0) + fee_units
                total_units += fee_units
                nav_per_unit = fund_value / total_units if total_units > 0 else nav_per_unit
        else:  # capital event
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


def _find_matching_pending_pledge(amount: float, path: str = PLEDGES_PATH) -> Optional[Dict[str, Any]]:
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


def _opening_balance_on_or_after(history: List[Dict[str, Any]], boundary_date) -> Optional[float]:
    """Return the earliest recorded balance on/after *boundary_date*.

    Falling back to the earliest snapshot within the period (rather than
    requiring an exact boundary-date match) means a period that started
    before the bot began tracking balance history still gets a usable
    opening balance instead of no Return figure at all.
    """
    boundary_s = boundary_date.isoformat()
    candidates = [r for r in history if (r.get("date") or "") >= boundary_s]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.get("date") or "")
    try:
        return float(candidates[0].get("balance"))
    except (TypeError, ValueError):
        return None


def compute_period_returns(current_balance: Optional[float], path: str = BALANCE_HISTORY_PATH) -> Dict[str, Optional[float]]:
    """Return calendar-aligned {daily, weekly, monthly} % returns.

    Each is ``(current_balance - opening_balance) / opening_balance * 100``
    where *opening_balance* is the Balance recorded at that period's start
    (today / this Monday / the 1st of this month). A period with no
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
        opening = _opening_balance_on_or_after(history, boundary)
        if opening is None:
            continue
        result[f"{period}_opening"] = round(opening, 2)
        if opening > 0:
            result[period] = round((current_balance - opening) / opening * 100, 2)
    return result
