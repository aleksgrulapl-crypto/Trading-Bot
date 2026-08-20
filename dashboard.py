# ============================
# DASHBOARD MODULE (CLEAN — EQUITY + BALANCE + PNL + AVAILABLE)
# ============================

import functools
import time
import math
import logging
from flask import Blueprint, request, render_template, redirect, jsonify

import session
import config
from trade_log import (
    load_raw_log,
    reconcile_with_positions,
    append_open_trade,
    get_completed_trades
)

dashboard = Blueprint("dashboard", __name__, template_folder="templates")

# logging
logger = logging.getLogger("dashboard")
if getattr(config, "DEBUG_LOGS", False):
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        return view(*args, **kwargs)
    return wrapper


def normalize_trades(trades):
    normalized = []

    for t in trades:
        # ensure consistent dealId type
        t["dealId"] = str(t.get("dealId")) if t.get("dealId") else None

        # normalize side
        side = t.get("side")
        if isinstance(side, str):
            s = side.lower()
            if s in ("long", "short"):
                t["side"] = s.capitalize()
            else:
                t["side"] = side
        else:
            t["side"] = "Short"

        # ensure numeric pnl
        pnl = t.get("pnl", 0)
        try:
            t["pnl"] = float(pnl) if pnl is not None else 0.0
        except Exception:
            t["pnl"] = 0.0

        # ensure entry/exit price numeric where present
        try:
            if t.get("entry_price") is not None:
                t["entry_price"] = float(t["entry_price"])
        except Exception:
            pass
        try:
            if t.get("exit_price") is not None:
                t["exit_price"] = float(t["exit_price"])
        except Exception:
            pass

        normalized.append(t)

    return normalized


def dedupe_trades(trades):
    """
    Deduplicate trades. Prefer closed record over open when duplicates found.
    Fallback signature uses ticker + rounded entry_price + time_entered.
    """
    seen = {}
    unique = []

    for t in trades:
        deal_id = t.get("dealId")
        if deal_id:
            key = ("ID", deal_id)
        else:
            # align with trade_log signature rounding
            entry = t.get("entry_price")
            try:
                entry_norm = round(float(entry or 0), 8)
            except Exception:
                entry_norm = str(entry)
            key = ("FALLBACK", str(t.get("ticker")), str(entry_norm), str(t.get("time_entered")))

        idx = seen.get(key)
        if idx is None:
            seen[key] = len(unique)
            unique.append(t)
        else:
            # if existing is open and new one is closed, replace
            if unique[idx].get("status") != "CLOSED" and t.get("status") == "CLOSED":
                unique[idx] = t

    return unique


def filter_completed(trades):
    return [t for t in trades if t.get("status") == "CLOSED"]


def compute_analytics(trades):
    if not trades:
        return {
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "expectancy": None,
            "total_pl": None,
            "max_drawdown": None,
            "trade_count": 0,
            "story": None
        }

    cleaned = []
    for t in trades:
        pnl = t.get("pnl", 0)
        try:
            t["pnl"] = float(pnl)
        except (TypeError, ValueError):
            t["pnl"] = 0.0
        cleaned.append(t)

    trades = cleaned

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]

    trade_count = len(trades)
    win_rate = round(len(wins) / trade_count * 100, 2) if trade_count else None
    avg_win = round(sum(wins) / len(wins), 2) if wins else None
    avg_loss = round(sum(losses) / len(losses), 2) if losses else None

    expectancy = None
    if avg_win is not None and avg_loss is not None and win_rate is not None:
        p_win = win_rate / 100
        p_loss = 1 - p_win
        expectancy = round(p_win * avg_win + p_loss * avg_loss, 2)

    running = 0.0
    max_peak = -math.inf
    max_drawdown = 0.0

    # compute running equity curve and drawdown
    for t in trades:
        running += t["pnl"]
        if max_peak == -math.inf:
            max_peak = running
        else:
            max_peak = max(max_peak, running)
        max_drawdown = min(max_drawdown, running - max_peak)

    total_pl = round(running, 2)

    return {
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "total_pl": total_pl,
        "max_drawdown": round(max_drawdown, 2),
        "trade_count": trade_count,
        "story": "Discipline and controlled losses define the curve."
    }


@dashboard.route("/dashboard")
@login_required
def dashboard_home():
    # force fresh account fetch
    session._cache["account"]["ts"] = 0

    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    positions = session.enrich_positions(raw_positions)
    account = session.enrich_account(raw_account)

    # reconcile local trade log with live positions so closed trades are recorded
    try:
        recon = reconcile_with_positions(positions)
        if getattr(config, "DEBUG_LOGS", False):
            logger.debug("trade_log reconcile result: %s", recon)
    except Exception:
        logger.exception("dashboard: reconcile_with_positions failed")

    combined_raw = load_raw_log()
    combined_trades = normalize_trades(dedupe_trades(combined_raw))

    combined_trades.sort(
        key=lambda t: (t.get("time_exited") or t.get("time_entered") or ""),
        reverse=True
    )

    analytics = compute_analytics(filter_completed(combined_trades))

    session.shared_state["account"] = account
    session.shared_state["positions"] = positions
    session.shared_state["trade_log"] = combined_trades
    session.shared_state["analytics"] = analytics

    return render_template(
        "dashboard.html",
        title=config.DASHBOARD_TITLE,
        cache_bust=time.time(),
        account=account,
        positions=positions,
        trades=combined_trades,
        analytics=analytics
    )


@dashboard.route("/dashboard/data")
@login_required
def dashboard_data():
    # force fresh account fetch
    session._cache["account"]["ts"] = 0

    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    positions = session.enrich_positions(raw_positions)
    account = session.enrich_account(raw_account)

    # reconcile local trade log with live positions so closed trades are recorded
    try:
        recon = reconcile_with_positions(positions)
        if getattr(config, "DEBUG_LOGS", False):
            logger.debug("trade_log reconcile result: %s", recon)
    except Exception:
        logger.exception("dashboard: reconcile_with_positions failed")

    combined_raw = load_raw_log()
    combined_trades = normalize_trades(dedupe_trades(combined_raw))

    combined_trades.sort(
        key=lambda t: (t.get("time_exited") or t.get("time_entered") or ""),
        reverse=True
    )

    analytics = compute_analytics(filter_completed(combined_trades))

    html = render_template(
        "dashboard_partial.html",
        cache_bust=time.time(),
        account=account,
        positions=positions,
        trades=combined_trades,
        analytics=analytics
    )

    return jsonify({
        "html": html,
        "account": account,
        "positions": positions,
        "trades": combined_trades,
        "analytics": analytics
    })
