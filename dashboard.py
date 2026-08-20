# ============================
# DASHBOARD MODULE (CLEAN — EQUITY + BALANCE + PNL + AVAILABLE)
# ============================

import functools
import time
from flask import Blueprint, request, render_template, redirect, jsonify

import session
import config
from trade_log import load_raw_log

dashboard = Blueprint("dashboard", __name__, template_folder="templates")


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
        t["dealId"] = str(t.get("dealId")) if t.get("dealId") else None

        side = t.get("side")
        if isinstance(side, str):
            s = side.lower()
            if s in ("long", "short"):
                t["side"] = s.capitalize()
            else:
                t["side"] = side
        else:
            t["side"] = "Short"

        pnl = t.get("pnl", 0)
        try:
            t["pnl"] = float(pnl)
        except Exception:
            t["pnl"] = 0.0

        normalized.append(t)

    return normalized


def dedupe_trades(trades):
    seen = {}
    unique = []

    for t in trades:
        deal_id = t.get("dealId")
        if deal_id:
            key = ("ID", deal_id)
        else:
            key = (
                "FALLBACK",
                str(t.get("ticker")),
                str(t.get("time_entered")),
                str(t.get("entry_price")),
            )

        idx = seen.get(key)
        if idx is None:
            seen[key] = len(unique)
            unique.append(t)
        else:
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

    running = 0
    max_peak = 0
    max_drawdown = 0

    for t in trades:
        running += t["pnl"]
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
