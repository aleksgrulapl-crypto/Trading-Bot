"""Weekly-return gating and the Owner-only rejected-trades log.

Stocks are ranked by Sharpe ratio (and net PnL) from closed TradingView/Hedge
trades. Depending on the current weekly return, new entries on weak stocks are
rejected or sized down (thresholds in config.py: GATE_*).
"""
import json
import logging
import math
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
from trade_log import load_raw_log, _atomic_write

logger = logging.getLogger("gating")

_AUTOMATED_SOURCES = frozenset(("tradingview", "hedge"))
_rejected_lock = threading.Lock()


def _norm(value) -> Optional[str]:
    return str(value).strip().upper() if value not in (None, "") else None


def _trade_pnl_gbp(trade: Dict[str, Any]) -> Optional[float]:
    fx = float(getattr(config, "FX_USD_GBP", 0.78) or 0.78)
    for key, mult in (("pnl_gbp", 1.0), ("pnl", fx)):
        raw = trade.get(key)
        if raw not in (None, ""):
            try:
                return float(raw) * mult
            except (TypeError, ValueError):
                return None
    return None


def stock_stats(trades: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Per-stock {count, net_pnl, sharpe} from closed TradingView/Hedge trades."""
    if trades is None:
        trades = load_raw_log()
    pnls: Dict[str, List[float]] = {}
    for t in trades or []:
        source = str(t.get("trade_type") or t.get("trade_source") or t.get("origin") or t.get("source") or "").strip().lower()
        if source not in _AUTOMATED_SOURCES:
            continue
        if not (t.get("status") == "CLOSED" or t.get("time_exited")):
            continue
        key = _norm(t.get("ticker") or t.get("epic"))
        pnl = _trade_pnl_gbp(t)
        if key and pnl is not None:
            pnls.setdefault(key, []).append(pnl)

    stats = {}
    for key, values in pnls.items():
        sharpe = 0.0
        if len(values) >= 2:
            avg = sum(values) / len(values)
            sd = math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))
            sharpe = avg / sd if sd > 0 else 0.0
        stats[key] = {"count": len(values), "net_pnl": round(sum(values), 2), "sharpe": round(sharpe, 2)}
    return stats


def weak_stocks(stats: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Return {stock: reason} for stocks in the bottom GATE_BOTTOM_FRACTION by
    Sharpe ratio or with negative net PnL. Stocks with fewer than
    GATE_MIN_TRADES closed trades are not ranked and never gated."""
    min_trades = int(getattr(config, "GATE_MIN_TRADES", 5))
    ranked = sorted(
        ((k, v) for k, v in stats.items() if v["count"] >= min_trades),
        key=lambda kv: (kv[1]["sharpe"], kv[1]["net_pnl"]),
    )
    if not ranked:
        return {}
    n_bottom = max(1, math.ceil(len(ranked) * float(getattr(config, "GATE_BOTTOM_FRACTION", 0.2))))
    weak: Dict[str, str] = {}
    for i, (key, v) in enumerate(ranked):
        if v["net_pnl"] < 0:
            weak[key] = f"net negative (PnL {v['net_pnl']}, Sharpe {v['sharpe']})"
        elif i < n_bottom:
            weak[key] = f"bottom {int(float(getattr(config, 'GATE_BOTTOM_FRACTION', 0.2)) * 100)}% by Sharpe ({v['sharpe']})"
    return weak


def current_weekly_return() -> Optional[float]:
    """This week's return in percent (closed-trade PnL / opening balance), or
    None when it can't be determined."""
    try:
        import deposits
        import session
        import dashboard
        account = session.enrich_account(session.get_account() or {}) or {}
        events = dashboard._trade_pnl_events(load_raw_log())
        return deposits.compute_period_returns(account.get("balance"), pnl_events=events).get("weekly")
    except Exception:
        logger.exception("gating: weekly return unavailable")
        return None


def evaluate(stock: Optional[str], weekly_return: Optional[float] = None,
             trades: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Decide whether a new entry on *stock* is allowed.

    Returns {"action": "allow"|"reject"|"reduce", "scale": float, "reason": str,
    "weekly_return", "sharpe", "net_pnl", "trades"}. Fails open (allow) when
    gating is disabled, the weekly return is unknown, or the stock is not weak.
    """
    result = {"action": "allow", "scale": 1.0, "reason": None, "weekly_return": weekly_return,
              "sharpe": None, "net_pnl": None, "trades": None}
    if not getattr(config, "WEEKLY_GATE_ENABLED", True):
        return result
    key = _norm(stock)
    if not key:
        return result
    stats = stock_stats(trades)
    info = stats.get(key)
    if info:
        result.update(sharpe=info["sharpe"], net_pnl=info["net_pnl"], trades=info["count"])
    weak_reason = weak_stocks(stats).get(key)
    if not weak_reason:
        return result
    if weekly_return is None:
        weekly_return = current_weekly_return()
        result["weekly_return"] = weekly_return
    if weekly_return is None:
        return result

    loss_pct = float(getattr(config, "GATE_LOSS_PCT", 0.0))
    reject_pct = float(getattr(config, "GATE_REJECT_PCT", 5.0))
    reduce_pct = float(getattr(config, "GATE_REDUCE_PCT", 10.0))
    if weekly_return < loss_pct:
        result.update(action="reject", reason=f"Negative week ({weekly_return}%); weak stock: {weak_reason}")
    elif weekly_return >= reduce_pct:
        result.update(action="reduce", scale=float(getattr(config, "GATE_REDUCE_SCALE", 0.5)),
                      reason=f"Weekly return {weekly_return}% >= {reduce_pct}%; weak stock sized down: {weak_reason}")
    elif weekly_return >= reject_pct:
        result.update(action="reject", reason=f"Weekly return {weekly_return}% in {reject_pct}-{reduce_pct}% band; weak stock: {weak_reason}")
    return result


def _path() -> str:
    return getattr(config, "REJECTED_TRADES_PATH", "rejected_trades.json")


def load_rejected(path: Optional[str] = None) -> List[Dict[str, Any]]:
    path = path or _path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        logger.exception("gating: failed to load rejected trades")
        return []


def count_unreviewed(path: Optional[str] = None) -> int:
    """Rejected trades the Owner has not yet marked as a would-be win/loss."""
    return sum(1 for r in load_rejected(path) if isinstance(r, dict) and r.get("outcome") not in ("win", "loss"))


def set_rejected_outcome(index: int, outcome: Optional[str], path: Optional[str] = None) -> str:
    """Mark a rejected trade (by position in the stored list) as a would-be
    'win' or 'loss'; None/'' clears the mark. Returns a status string."""
    if outcome in ("", None):
        outcome = None
    elif outcome not in ("win", "loss"):
        return "invalid_outcome"
    path = path or _path()
    with _rejected_lock:
        rows = load_rejected(path)
        if not isinstance(index, int) or index < 0 or index >= len(rows) or not isinstance(rows[index], dict):
            return "not_found"
        if outcome is None:
            rows[index].pop("outcome", None)
        else:
            rows[index]["outcome"] = outcome
        return "ok" if _atomic_write(path, rows) else "write_failed"


def record_rejected(entry: Dict[str, Any], path: Optional[str] = None) -> bool:
    """Append a rejected trade (with TradingView's suggested SL/TP) to the
    Owner-only rejected log."""
    path = path or _path()
    entry = dict(entry)
    entry.setdefault("time_rejected", datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z")
    with _rejected_lock:
        rows = load_rejected(path)
        rows.append(entry)
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        except Exception:
            pass
        return _atomic_write(path, rows)
