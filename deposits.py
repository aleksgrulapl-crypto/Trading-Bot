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
    investor: str = "",
    note: str = "",
    occurred_at: Optional[str] = None,
    path: str = LOG_PATH,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Append a new deposit/withdrawal row to the ledger.

    *amount* must be a positive number; direction is carried by *entry_type*
    ("deposit" or "withdrawal"), not by the sign of the amount. *investor*
    identifies whose capital this entry belongs to, so ROI/ownership share
    can be calculated per-person.
    """
    normalized_type = str(entry_type or "").strip().lower()
    if normalized_type not in VALID_TYPES:
        return False, None, "invalid_type"

    amt = _coerce_amount(amount)
    if amt is None:
        return False, None, "invalid_amount"

    investor_name = str(investor or "").strip()
    if not investor_name:
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


def list_investors(entries: List[Dict[str, Any]]) -> List[str]:
    """Return a sorted list of distinct investor names seen in *entries*."""
    names = {str(e.get("investor") or "").strip() for e in entries or [] if isinstance(e, dict)}
    names.discard("")
    return sorted(names, key=str.lower)


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
    """
    as_of = as_of or datetime.utcnow()

    owner_name = str(getattr(config, "OWNER_INVESTOR_NAME", "Aleks") or "").strip()
    owner_override_pct = float(getattr(config, "OWNER_INVESTOR_OVERRIDE_PCT", 15) or 0)

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

    # Seed the owner's value with any pre-ledger PnL (trading that happened
    # before anyone's deposits were tracked).
    owner_seed_value = pre_ledger_pnl if owner_key else 0.0

    all_names = sorted(deposited_totals.keys() | withdrawn_totals.keys(), key=str.lower)

    balance = None
    if current_balance is not None:
        try:
            balance = float(current_balance)
        except (TypeError, ValueError):
            balance = None

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
        "pre_ledger_pnl": owner_seed_value if owner_key else None,
    }
