#!/usr/bin/env python3
# dashboard.py
# ============================
# DASHBOARD MODULE (CLEAN — EQUITY + BALANCE + PNL + AVAILABLE)
# ============================

import functools
import time
import math
from statistics import mean
import logging
import os
from flask import Blueprint, request, render_template, redirect, jsonify

import session
import config
from trade_log import (
    load_raw_log,
    reconcile_with_positions,
    # append_open_trade and get_completed_trades intentionally not imported here
)

dashboard = Blueprint("dashboard", __name__, template_folder="templates")

# logging: configure only this logger, do not call basicConfig here
logger = logging.getLogger("dashboard")
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [dashboard] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)


# -----------------------------
# Login routes (added)
# -----------------------------
@dashboard.route("/dashboard/login", methods=["GET"])
def dashboard_login():
    return render_template("login.html", title="Dashboard Login")


@dashboard.route("/dashboard/login", methods=["POST"])
def dashboard_login_submit():
    password = request.form.get("password", "")
    # Simple password check — replace with your real secret in env
    if password == os.getenv("DASHBOARD_PASSWORD", "Angelika140282"):
        resp = redirect("/dashboard")
        resp.set_cookie("dashboard_auth", "1", max_age=60*60*24*7)  # 7 days
        return resp
    return render_template("login.html", title="Dashboard Login", error="Invalid password")


@dashboard.route("/dashboard/logout")
def dashboard_logout():
    resp = redirect("/dashboard/login")
    resp.delete_cookie("dashboard_auth")
    return resp


# -----------------------------
# Auth decorator
# -----------------------------
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
    """
    Compute analytics from a list of trade dicts.
    Expects each trade to have a numeric 'pnl' (USD) when closed.
    Returns win_rate (percent), avg_win, avg_loss, expectancy, total_pl, max_drawdown (negative), trade_count, story.
    """

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

    # Normalize and filter trades: only consider trades with numeric pnl values for numeric metrics.
    cleaned = []
    for t in trades:
        # Keep original dict but ensure numeric pnl when possible
        pnl_raw = t.get("pnl", None)
        pnl_num = None
        try:
            if pnl_raw is not None and str(pnl_raw).strip() != "":
                pnl_num = float(pnl_raw)
        except Exception:
            pnl_num = None
        # copy to avoid mutating caller's list
        copy_t = dict(t)
        copy_t["pnl"] = pnl_num
        cleaned.append(copy_t)

    # Use closed trades only (if your trades list includes open trades, filter them out)
    closed = [t for t in cleaned if t.get("status") == "CLOSED" or t.get("time_exited")]

    # Extract numeric pnls
    pnls = [t["pnl"] for t in closed if t.get("pnl") is not None]

    # Separate wins, losses, zeros
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    zeros = [p for p in pnls if p == 0]

    trade_count = len(closed)

    # Win rate: wins / (wins + losses) — ignore zero-PnL trades for win rate calculation
    denom = len(wins) + len(losses)
    win_rate = None
    if denom > 0:
        win_rate = round((len(wins) / denom) * 100, 2)

    avg_win = round(mean(wins), 2) if wins else None
    avg_loss = round(mean(losses), 2) if losses else None

    expectancy = None
    if denom > 0 and avg_win is not None and avg_loss is not None:
        p_win = len(wins) / denom
        # avg_loss is negative; expectancy will reflect average PnL per trade
        expectancy = round(p_win * avg_win + (1 - p_win) * avg_loss, 4)

    # Running equity and max drawdown (negative value)
    running = 0.0
    peak = -math.inf
    max_drawdown = 0.0  # will store the most negative (min) running - peak
    # Use the chronological order of closed trades as provided
    for t in closed:
        pnl_val = t.get("pnl")
        if pnl_val is None:
            pnl_val = 0.0
        running += float(pnl_val)
        if peak == -math.inf:
            peak = running
        else:
            peak = max(peak, running)
        # drawdown = running - peak (<= 0)
        drawdown = running - peak
        if drawdown < max_drawdown:
            max_drawdown = drawdown

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
