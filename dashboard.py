#!/usr/bin/env python3
# dashboard.py
# Production-ready dashboard blueprint.
# Key features:
#   - Robust JSON error handling for /dashboard/data
#   - Hardened normalization, dedupe, and analytics input sanitization
#   - Safe per-request context: shared_state updated per request, not globally
#   - Safe analytics defaults: all keys always present, None values handled
#   - Close-position endpoint for the dashboard "Close" button
#   - Clearer login env configuration and logging

import functools
import time
import math
from statistics import mean
from datetime import datetime, timedelta
import logging
import os
from flask import Blueprint, request, render_template, redirect, jsonify

import session
import config
import deposits
import notifications
from display_helpers import format_human
from close_position import close_position as close_live_position
from trade_log import (
    delete_trade_log_entry,
    update_trade_type_entry,
    update_trade_exit_price_entry,
    dedupe_trade_log_entries,
    is_trade_delete_candidate,
    load_raw_log,
    reconcile_with_positions,
    _parse_iso_like,
)

dashboard = Blueprint("dashboard", __name__, template_folder="templates")

logger = logging.getLogger("dashboard")
logger.setLevel(logging.DEBUG if getattr(config, "DEBUG_LOGS", False) else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [dashboard] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)


# -----------------------------
# Login routes
# -----------------------------
@dashboard.route("/dashboard/login", methods=["GET"])
def dashboard_login():
    return render_template("login.html", title="Dashboard Login")


@dashboard.route("/dashboard/login", methods=["POST"])
def dashboard_login_submit():
    username = (request.form.get("username") or "owner").strip().lower()
    password = request.form.get("password", "")

    owner_password = os.getenv("DASHBOARD_OWNER_PASSWORD", getattr(config, "DASHBOARD_OWNER_PASSWORD", None) or "Angelika140282")
    viewer_password = os.getenv("DASHBOARD_VIEWER_PASSWORD", getattr(config, "DASHBOARD_VIEWER_PASSWORD", None) or "Viewer123$")
    investor_password = os.getenv("DASHBOARD_INVESTOR_PASSWORD", getattr(config, "DASHBOARD_INVESTOR_PASSWORD", None) or "Investor123$")
    if owner_password == "Angelika140282":
        logger.warning("Using default dashboard Owner password. Set DASHBOARD_OWNER_PASSWORD in environment to secure the dashboard.")

    if username == "owner" and password == owner_password:
        role = "owner"
    elif username == "viewer" and password == viewer_password:
        role = "viewer"
    elif username == "investor" and password == investor_password:
        role = "investor"
    else:
        return render_template("login.html", title="Dashboard Login", error="Invalid username or password"), 401

    resp = redirect("/dashboard")
    resp.set_cookie("dashboard_auth", "1", max_age=60 * 60 * 24 * 7)
    resp.set_cookie("dashboard_role", role, max_age=60 * 60 * 24 * 7)
    return resp


@dashboard.route("/dashboard/logout")
def dashboard_logout():
    resp = redirect("/dashboard/login")
    resp.delete_cookie("dashboard_auth")
    resp.delete_cookie("dashboard_role")
    return resp


# -----------------------------
# Auth decorators
# -----------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        return view(*args, **kwargs)
    return wrapper


def current_role() -> str:
    """Return the logged-in role: 'owner', 'viewer', or 'investor'.

    Legacy sessions (an auth cookie set before roles existed, or a cookie
    with an unrecognized value) are treated as 'owner' to preserve the
    original single-password behavior.
    """
    role = request.cookies.get("dashboard_role")
    if role in ("owner", "viewer", "investor"):
        return role
    return "owner"


def owner_required(view):
    """Require the Owner role for mutating dashboard actions (close/delete/edit).
    Viewer/Investor accounts are authenticated but limited to read-only pages."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        if current_role() != "owner":
            return jsonify({
                "status": "error",
                "message": "forbidden_viewer_role",
            }), 403
        return view(*args, **kwargs)
    return wrapper


def owner_page_required(view):
    """Require the Owner role for whole-page (non-JSON) dashboard routes.
    Viewer/Investor accounts are authenticated but are redirected back to the
    main dashboard instead of receiving a JSON 403."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        if current_role() != "owner":
            return redirect("/dashboard")
        return view(*args, **kwargs)
    return wrapper


@dashboard.context_processor
def _inject_role():
    """Make the logged-in role available to every dashboard template
    without threading it through each render_template() call."""
    try:
        return {"role": current_role()}
    except Exception:
        return {"role": "owner"}


def roi_access_required(view):
    """Require the Owner or Investor role for the ROI page (whole-page,
    non-JSON). Viewer accounts are authenticated but are redirected back to
    the main dashboard — only Owner and Investor roles may see per-investor
    gain/loss figures."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        if current_role() not in ("owner", "investor"):
            return redirect("/dashboard")
        return view(*args, **kwargs)
    return wrapper


# -----------------------------
# Helpers: normalization, dedupe, filtering
# -----------------------------
def _safe_str(v):
    return str(v) if v is not None else None


def _live_position_deal_ids(raw_positions, positions):
    if raw_positions is None:
        return None

    def _collect_ids(rows):
        ids = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            deal_id = row.get("dealId") or ((row.get("position") or {}).get("dealId") if isinstance(row.get("position"), dict) else None)
            if deal_id not in (None, ""):
                ids.add(str(deal_id))
        return ids

    live_ids = _collect_ids(raw_positions)
    live_ids.update(_collect_ids(positions))
    if not live_ids:
        return None

    return live_ids


def _trade_type_label(trade):
    raw_source = trade.get("trade_type") or trade.get("trade_source") or trade.get("origin") or trade.get("source")
    if raw_source not in (None, ""):
        source = str(raw_source).strip().lower()
        if source in ("tradingview", "webhook", "bot"):
            return "TradingView"
        if source == "hedge":
            return "Hedge"
        if source in ("manual", "broker", "trader"):
            return "Trader"
        if source == "unknown":
            return "Trader"

    notes = str(trade.get("notes") or "").lower()
    if "webhook" in notes or "tradingview" in notes:
        return "TradingView"
    return "Trader"


# ROI-tier trade-source groupings. "Full" tier investors (and the Owner)
# additionally share in TRADER_TRADE_SOURCES PnL; every investor shares in
# AUTOMATED_TRADE_SOURCES PnL (Hedge is automated risk management, not a
# human discretionary trade, so it is grouped with TradingView here).
AUTOMATED_TRADE_SOURCES = frozenset(("TradingView", "Hedge"))
TRADER_TRADE_SOURCES = frozenset(("Trader",))


def normalize_trades(trades):
    """
    Normalize trade dicts for display and analytics.
    Do not mutate caller objects; return new list of dicts.
    Ensure numeric fields are numeric or None.
    Keep side as None when unknown.
    """
    out = []
    for t in trades or []:
        copy = dict(t) if isinstance(t, dict) else {}
        # canonical dealId as string or None
        copy["dealId"] = _safe_str(copy.get("dealId")) if copy.get("dealId") not in (None, "") else None

        # normalize side to 'Long'/'Short' or None
        side = copy.get("side")
        if isinstance(side, str):
            s = side.strip().lower()
            if s == "long":
                copy["side"] = "Long"
            elif s == "short":
                copy["side"] = "Short"
            else:
                copy["side"] = side
        else:
            copy["side"] = None

        # numeric pnl if possible, else None
        pnl = copy.get("pnl", None)
        try:
            copy["pnl"] = float(pnl) if pnl not in (None, "") else None
        except Exception:
            copy["pnl"] = None

        # numeric entry/exit price if present
        for key in ("entry_price", "exit_price", "price"):
            val = copy.get(key)
            try:
                if val not in (None, ""):
                    copy[key] = float(val)
                else:
                    copy[key] = None
            except Exception:
                copy[key] = None

        # ensure status is present
        copy["status"] = copy.get("status") or ("CLOSED" if copy.get("time_exited") else "OPEN")

        # ensure timeframe is present (older log entries predate this field)
        copy["timeframe"] = copy.get("timeframe") or "N/A"

        # human timestamps preserved by trade_log but ensure keys exist
        copy["time_entered"] = copy.get("time_entered")
        copy["time_exited"] = copy.get("time_exited")
        copy["time_entered_human"] = copy.get("time_entered_human")
        copy["time_exited_human"] = copy.get("time_exited_human")
        copy["trade_type"] = _trade_type_label(copy)

        out.append(copy)
    return out


def _mark_delete_candidates(trades, live_deal_ids):
    marked = []
    for trade in trades or []:
        copy = dict(trade) if isinstance(trade, dict) else {}
        copy["can_delete"] = is_trade_delete_candidate(
            copy,
            live_deal_ids=live_deal_ids,
            require_live_match_check=True,
        )
        marked.append(copy)
    return marked


def _signature_for_dedupe(t):
    """
    Create a stable signature for dedupe that aligns with trade_log._make_signature:
    use dealId when present, otherwise ticker + rounded entry_price.
    """
    if t.get("dealId"):
        return ("ID", str(t.get("dealId")))
    try:
        entry = round(float(t.get("entry_price") or 0), 8)
    except Exception:
        entry = str(t.get("entry_price") or "")
    return ("FALLBACK", str(t.get("ticker") or ""), str(entry))


def dedupe_trades(trades):
    """
    Deduplicate trades using trade_log's broker-safe rules.
    """
    unique, _ = dedupe_trade_log_entries(trades or [])
    return unique


def filter_completed(trades):
    return [t for t in trades if t.get("status") == "CLOSED"]


# -----------------------------
# Analytics
# -----------------------------
def compute_analytics(trades):
    """
    Compute analytics from a list of trade dicts.
    Uses only closed trades with numeric pnl for win/loss metrics.
    Returns JSON-serializable dict with numeric values or None.
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

    # copy and coerce pnl to numeric where possible.
    # Prefer pnl_gbp (already FX-converted) so analytics match the dashboard's
    # "(GBP)" labels; fall back to converting raw USD pnl if pnl_gbp is absent.
    fx_rate = float(getattr(config, "FX_USD_GBP", 0.78) or 0.78)
    cleaned = []
    for t in trades:
        copy = dict(t)
        pnl_gbp_raw = copy.get("pnl_gbp", None)
        pnl_raw = copy.get("pnl", None)
        try:
            if pnl_gbp_raw not in (None, ""):
                copy["pnl"] = float(pnl_gbp_raw)
            elif pnl_raw not in (None, ""):
                copy["pnl"] = round(float(pnl_raw) * fx_rate, 2)
            else:
                copy["pnl"] = None
        except Exception:
            copy["pnl"] = None
        cleaned.append(copy)

    closed = [t for t in cleaned if t.get("status") == "CLOSED" or t.get("time_exited")]
    pnls = [t["pnl"] for t in closed if t.get("pnl") is not None]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    trade_count = len(closed)

    denom = len(wins) + len(losses)
    win_rate = round((len(wins) / denom) * 100, 2) if denom > 0 else None

    avg_win = round(mean(wins), 2) if wins else None
    avg_loss = round(mean(losses), 2) if losses else None

    expectancy = None
    if denom > 0 and avg_win is not None and avg_loss is not None:
        p_win = len(wins) / denom
        expectancy = round(p_win * avg_win + (1 - p_win) * avg_loss, 4)

    # running equity and max drawdown
    running = 0.0
    peak = -math.inf
    max_drawdown = 0.0
    for t in closed:
        pnl_val = t.get("pnl") if t.get("pnl") is not None else 0.0
        running += float(pnl_val)
        if peak == -math.inf:
            peak = running
        else:
            peak = max(peak, running)
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


def _safe_analytics(analytics: dict) -> dict:
    """Ensure all expected analytics keys are present with safe defaults.

    Prevents template errors when a key is missing or analytics is None.
    """
    defaults = {
        "win_rate": None,
        "avg_win": None,
        "avg_loss": None,
        "expectancy": None,
        "total_pl": None,
        "max_drawdown": None,
        "trade_count": 0,
        "story": None,
    }
    if not isinstance(analytics, dict):
        return defaults
    result = dict(defaults)
    result.update(analytics)
    return result


def _naive_dt(dt):
    """Normalize a datetime to naive (UTC-equivalent) by dropping tzinfo,
    so comparisons across trade timestamps (which may include a 'Z'/offset)
    and naive `datetime.utcnow()`-based cutoffs never raise."""
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _trades_closed_since(trades, since_dt):
    """Return closed trades whose exit (or entry, as fallback) time falls on
    or after *since_dt*. Trades with no resolvable timestamp are excluded."""
    out = []
    for t in trades or []:
        ts = _naive_dt(_parse_iso_like(t.get("time_exited")) or _parse_iso_like(t.get("time_entered")))
        if ts is None:
            continue
        if ts >= since_dt:
            out.append(t)
    return out


def _trade_month_key(trade):
    """Return the "YYYY-MM" calendar month a completed trade belongs to
    (based on exit time, falling back to entry time), or None if
    unresolvable. Used for the Analytics per-month filter."""
    ts = _naive_dt(_parse_iso_like(trade.get("time_exited")) or _parse_iso_like(trade.get("time_entered")))
    if ts is None:
        return None
    return ts.strftime("%Y-%m")


def _available_analytics_months(trades):
    """Return the distinct "YYYY-MM" months present across *trades*' exit
    (or entry) times, newest first, for the Analytics month-selector."""
    months = {_trade_month_key(t) for t in trades or []}
    months.discard(None)
    return sorted(months, reverse=True)


def _trades_in_month(trades, month_key):
    """Return trades whose exit (or entry, as fallback) time falls within
    the calendar month *month_key* ("YYYY-MM")."""
    return [t for t in trades or [] if _trade_month_key(t) == month_key]


def _default_period_returns():
    """Safe all-"—" fallback for period_returns, used when a caller (or a
    legacy test monkeypatching _build_request_context) doesn't supply one."""
    return {
        "daily": None, "weekly": None, "monthly": None,
        "daily_opening": None, "weekly_opening": None, "monthly_opening": None,
        "goal": {"goal_pct": deposits.RETURN_GOAL_STEP_PCT, "progress_pct": 0.0},
        "successful_weeks": {"successful_weeks": 0, "total_weeks": 0, "avg_weekly_return": None},
    }


def _trade_pnl_events(trades, sources=None):
    """Build a chronological PnL event series (GBP, FX-converted) for closed
    trades with a resolvable exit time, for use in investor NAV accounting.

    ``sources``, when given, restricts events to trades whose
    ``_trade_type_label`` is in that set (e.g. ``{"TradingView", "Hedge"}``
    for the automated sleeve, or ``{"Trader"}`` for the discretionary
    sleeve) — used to compute per-tier ROI."""
    fx_rate = float(getattr(config, "FX_USD_GBP", 0.78) or 0.78)
    events = []
    for t in trades or []:
        if not (t.get("status") == "CLOSED" or t.get("time_exited")):
            continue
        if sources is not None and _trade_type_label(t) not in sources:
            continue
        ts = _naive_dt(_parse_iso_like(t.get("time_exited")) or _parse_iso_like(t.get("time_entered")))
        if ts is None:
            continue
        pnl_gbp_raw = t.get("pnl_gbp")
        pnl_raw = t.get("pnl")
        pnl = None
        try:
            if pnl_gbp_raw not in (None, ""):
                pnl = float(pnl_gbp_raw)
            elif pnl_raw not in (None, ""):
                pnl = round(float(pnl_raw) * fx_rate, 2)
        except Exception:
            pnl = None
        if pnl is None:
            continue
        events.append({"ts": ts, "pnl": pnl})
    return events


def _trade_hold_seconds(trade):
    """Seconds between time_entered and time_exited, or None if unavailable."""
    entered = _parse_iso_like(trade.get("time_entered"))
    exited = _parse_iso_like(trade.get("time_exited"))
    if not entered or not exited:
        return None
    try:
        return (exited - entered).total_seconds()
    except Exception:
        return None


def _format_duration(seconds):
    """Format a duration in seconds as e.g. '1d 4h 12m'."""
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def compute_detailed_analytics(trades):
    """
    Build in-depth analytics for the Analytics page: best/worst trade,
    per-ticker win/loss breakdown, an equity curve, and average hold time.
    Uses only closed trades with a resolvable GBP PnL.
    """
    defaults = {
        "best_trade": None,
        "worst_trade": None,
        "per_ticker_stats": [],
        "equity_curve": [],
        "avg_hold_time": None,
    }
    if not trades:
        return defaults

    fx_rate = float(getattr(config, "FX_USD_GBP", 0.78) or 0.78)

    cleaned = []
    for t in trades:
        copy = dict(t)
        pnl_gbp_raw = copy.get("pnl_gbp")
        pnl_raw = copy.get("pnl")
        try:
            if pnl_gbp_raw not in (None, ""):
                copy["pnl_gbp_display"] = float(pnl_gbp_raw)
            elif pnl_raw not in (None, ""):
                copy["pnl_gbp_display"] = round(float(pnl_raw) * fx_rate, 2)
            else:
                copy["pnl_gbp_display"] = None
        except Exception:
            copy["pnl_gbp_display"] = None
        cleaned.append(copy)

    closed = [t for t in cleaned if t.get("status") == "CLOSED" or t.get("time_exited")]
    closed_with_pnl = [t for t in closed if t.get("pnl_gbp_display") is not None]

    best_trade = max(closed_with_pnl, key=lambda t: t["pnl_gbp_display"]) if closed_with_pnl else None
    worst_trade = min(closed_with_pnl, key=lambda t: t["pnl_gbp_display"]) if closed_with_pnl else None

    # Per-ticker win/loss breakdown
    per_ticker = {}
    for t in closed_with_pnl:
        ticker = t.get("ticker") or "—"
        stats = per_ticker.setdefault(ticker, {
            "ticker": ticker, "wins": 0, "losses": 0, "trade_count": 0, "total_pnl": 0.0,
        })
        stats["trade_count"] += 1
        pnl = t["pnl_gbp_display"]
        stats["total_pnl"] += pnl
        if pnl > 0:
            stats["wins"] += 1
        elif pnl < 0:
            stats["losses"] += 1

    per_ticker_stats = []
    for stats in per_ticker.values():
        decided = stats["wins"] + stats["losses"]
        stats["win_rate"] = round((stats["wins"] / decided) * 100, 2) if decided else None
        stats["total_pnl"] = round(stats["total_pnl"], 2)
        per_ticker_stats.append(stats)
    per_ticker_stats.sort(key=lambda s: s["total_pnl"], reverse=True)

    # Equity curve: cumulative GBP PnL ordered chronologically by exit time
    ordered = sorted(
        closed_with_pnl,
        key=lambda t: t.get("time_exited") or t.get("time_entered") or "",
    )
    equity_curve = []
    running = 0.0
    for t in ordered:
        running += t["pnl_gbp_display"]
        label = t.get("time_exited_human") or t.get("time_exited") or t.get("time_entered_human") or ""
        equity_curve.append({"label": label, "balance": round(running, 2)})

    # Average hold time across closed trades with resolvable entry/exit timestamps
    hold_seconds = [s for s in (_trade_hold_seconds(t) for t in closed) if s is not None]
    avg_hold_seconds = mean(hold_seconds) if hold_seconds else None

    return {
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "per_ticker_stats": per_ticker_stats,
        "equity_curve": equity_curve,
        "avg_hold_time": _format_duration(avg_hold_seconds),
    }


def _build_request_context():
    """Build fresh, per-request dashboard context.

    Each call fetches live data independently so concurrent requests
    do not share mutable state.

    Returns:
        dict with keys: account, positions, combined_trades, analytics
    """
    # Force fresh account cache for this request
    try:
        session._cache["account"]["ts"] = 0
    except Exception:
        logger.debug("session cache not initialized")

    raw_positions = session.get_positions()
    raw_account = session.get_account() or {}

    positions = session.enrich_positions(raw_positions or [])
    account = session.enrich_account(raw_account)

    # Display Open Positions sorted alphabetically by ticker for easier scanning.
    positions.sort(key=lambda p: str((p or {}).get("ticker") or "").strip().lower())

    # Reconcile local trade log against live positions (may update the log file)
    try:
        recon = reconcile_with_positions(positions)
        if getattr(config, "DEBUG_LOGS", False):
            logger.debug("trade_log reconcile result: %s", recon)
    except Exception:
        logger.exception("dashboard: reconcile_with_positions failed")

    combined_raw = [dict(t, _log_index=i) for i, t in enumerate(load_raw_log())]
    combined_trades = normalize_trades(dedupe_trades(combined_raw))
    combined_trades = _mark_delete_candidates(combined_trades, _live_position_deal_ids(raw_positions, positions))
    combined_trades.sort(
        key=lambda t: (t.get("time_exited") or t.get("time_entered") or ""),
        reverse=True,
    )

    completed_trades = filter_completed(combined_trades)
    analytics = _safe_analytics(compute_analytics(completed_trades))

    # Auto-detect balance changes unexplained by trading (manual deposits/
    # withdrawals made outside the app) and record them in the ledger,
    # auto-confirming a matching pending pledge when one exists.
    try:
        deposits.detect_and_record_balance_change(
            (account or {}).get("balance"),
            _trade_pnl_events(combined_trades),
        )
    except Exception:
        logger.exception("dashboard: balance auto-detection failed")

    # Calendar-aligned Daily/Weekly/Monthly Return, based on the Balance
    # recorded at each period's start (00:00 today / this Monday / the 1st
    # of this month) - distinct from weekly_analytics/monthly_analytics
    # below, which are rolling trade-count/win-rate windows, not a Return %.
    try:
        deposits.record_daily_balance_snapshot((account or {}).get("balance"))
        period_returns = deposits.compute_period_returns((account or {}).get("balance"))
        period_returns["goal"] = deposits.compute_weekly_return_goal_progress(period_returns.get("weekly"))
        deposits.record_weekly_close((account or {}).get("balance"))
        period_returns["successful_weeks"] = deposits.compute_successful_weeks()
    except Exception:
        logger.exception("dashboard: period return computation failed")
        period_returns = _default_period_returns()

    now = datetime.utcnow()
    weekly_analytics = _safe_analytics(
        compute_analytics(_trades_closed_since(completed_trades, now - timedelta(days=7)))
    )
    monthly_analytics = _safe_analytics(
        compute_analytics(_trades_closed_since(completed_trades, now - timedelta(days=30)))
    )

    return {
        "account": account,
        "positions": positions,
        "combined_trades": combined_trades,
        "analytics": analytics,
        "weekly_analytics": weekly_analytics,
        "monthly_analytics": monthly_analytics,
        "period_returns": period_returns,
    }


# -----------------------------
# Views
# -----------------------------
@dashboard.route("/dashboard")
@login_required
def dashboard_home():
    """Render the full dashboard page with fresh per-request context."""
    ctx = _build_request_context()

    # Update shared_state so other modules can read the latest snapshot.
    # This is a best-effort update; it must not fail the request.
    try:
        if not isinstance(session.shared_state, dict):
            session.shared_state = {}
        session.shared_state["account"] = ctx["account"]
        session.shared_state["positions"] = ctx["positions"]
        session.shared_state["trade_log"] = ctx["combined_trades"]
        session.shared_state["analytics"] = ctx["analytics"]
    except Exception:
        logger.debug("dashboard: could not update shared_state")

    return render_template(
        "dashboard.html",
        title=getattr(config, "DASHBOARD_TITLE", "Dashboard"),
        cache_bust=time.time(),
        account=ctx["account"],
        positions=ctx["positions"],
        analytics=ctx["analytics"],
        weekly_analytics=ctx["weekly_analytics"],
        monthly_analytics=ctx["monthly_analytics"],
        period_returns=ctx.get("period_returns") or _default_period_returns(),
        is_owner=current_role() == "owner",
        email_notify_enabled=notifications.is_enabled(),
        email_notify_recipient=notifications.get_recipient(),
    )


@dashboard.route("/dashboard/data")
@login_required
def dashboard_data():
    """Return fresh dashboard data as JSON (with rendered HTML partial).

    Always returns JSON, even on error, to prevent client-side parse failures.
    """
    ctx = _build_request_context()

    try:
        html = render_template(
            "dashboard_partial.html",
            cache_bust=time.time(),
            account=ctx["account"],
            positions=ctx["positions"],
            analytics=ctx["analytics"],
            weekly_analytics=ctx["weekly_analytics"],
            monthly_analytics=ctx["monthly_analytics"],
            period_returns=ctx.get("period_returns") or _default_period_returns(),
            is_owner=current_role() == "owner",
            email_notify_enabled=notifications.is_enabled(),
            email_notify_recipient=notifications.get_recipient(),
        )
        return jsonify({
            "html": html,
            "account": ctx["account"],
            "positions": ctx["positions"],
            "analytics": ctx["analytics"],
        })
    except Exception as exc:
        logger.exception("dashboard/data render failed: %s", exc)
        return jsonify({
            "error": "render_failed",
            "message": "Failed to render dashboard partial",
            "details": str(exc),
        }), 500


@dashboard.route("/dashboard/trades")
@login_required
def dashboard_trades():
    """Render the Trade Log page (moved off the main dashboard for a
    tidier, more focused layout)."""
    ctx = _build_request_context()

    return render_template(
        "trade_log.html",
        title=getattr(config, "DASHBOARD_TITLE", "Dashboard"),
        cache_bust=time.time(),
        account=ctx["account"],
        trades=ctx["combined_trades"],
        is_owner=current_role() == "owner",
    )


@dashboard.route("/dashboard/trades/data")
@login_required
def dashboard_trades_data():
    """Return fresh Trade Log data as JSON (with rendered HTML partial)."""
    ctx = _build_request_context()

    try:
        html = render_template(
            "trade_log_partial.html",
            cache_bust=time.time(),
            trades=ctx["combined_trades"],
            is_owner=current_role() == "owner",
        )
        return jsonify({
            "html": html,
            "account": ctx["account"],
            "trades": ctx["combined_trades"],
        })
    except Exception as exc:
        logger.exception("dashboard/trades/data render failed: %s", exc)
        return jsonify({
            "error": "render_failed",
            "message": "Failed to render trade log partial",
            "details": str(exc),
        }), 500


@dashboard.route("/dashboard/investors")
@login_required
def dashboard_investors():
    """Render a simple, visual-only list of investor names (no financial
    figures) — visible to any logged-in user. Each investor is tagged with
    their ROI tier (owner / full / tradingview) for colour-coding; the tier
    is only changeable by the Owner."""
    ctx = _build_request_context()
    entries = deposits.list_entries_sorted()
    investors = deposits.list_investors(entries)
    owner_name = str(getattr(config, "OWNER_INVESTOR_NAME", "Aleks") or "").strip()
    tiers = deposits.load_tiers()

    investor_rows = []
    for name in investors:
        is_owner = bool(owner_name) and name.strip().lower() == owner_name.strip().lower()
        tier = "owner" if is_owner else deposits.get_investor_tier(name, tiers)
        investor_rows.append({"name": name, "tier": tier, "is_owner": is_owner})

    is_owner_role = current_role() == "owner"
    pledges = deposits.list_pledges_sorted() if is_owner_role else []
    for p in pledges:
        p["created_at_human"] = format_human(p.get("created_at")) or p.get("created_at")
        p["resolved_at_human"] = format_human(p.get("resolved_at")) or p.get("resolved_at")
    pending_pledges, completed_pledges = deposits.split_pledges(pledges)
    payment_details = {
        "bank_beneficiary": getattr(config, "INVESTOR_BANK_BENEFICIARY", "") or "",
        "bank_payment_reference": getattr(config, "INVESTOR_BANK_PAYMENT_REFERENCE", "") or "",
        "bank_account_number": getattr(config, "INVESTOR_BANK_ACCOUNT_NUMBER", "") or "",
        "bank_sort_code": getattr(config, "INVESTOR_BANK_SORT_CODE", "") or "",
        "bank_name_address": getattr(config, "INVESTOR_BANK_NAME_ADDRESS", "") or "",
        "bank_payment_note": getattr(config, "INVESTOR_BANK_PAYMENT_NOTE", "") or "",
        "crypto_btc_address": getattr(config, "INVESTOR_CRYPTO_BTC_ADDRESS", "") or "",
        "crypto_eth_address": getattr(config, "INVESTOR_CRYPTO_ETH_ADDRESS", "") or "",
    }

    return render_template(
        "investors.html",
        title=getattr(config, "DASHBOARD_TITLE", "Dashboard"),
        cache_bust=time.time(),
        account=ctx["account"],
        investor_rows=investor_rows,
        owner_name=owner_name,
        is_owner=is_owner_role,
        pending_pledges=pending_pledges,
        completed_pledges=completed_pledges,
        payment_details=payment_details,
        pledge_min_amount=float(getattr(config, "PLEDGE_MIN_AMOUNT", 50.0) or 50.0),
        pledge_max_amount=float(getattr(config, "PLEDGE_MAX_AMOUNT", 500.0) or 500.0),
    )


@dashboard.route("/dashboard/investors/pledge", methods=["POST"])
@login_required
def dashboard_investors_add_pledge():
    """Record a "Become an Investor" pledge (name + intended amount). This
    does not move real money — it is a notice to the Owner that a bank
    transfer is coming, so it can be matched up and confirmed later."""
    payload = request.form if request.form else (request.get_json(silent=True) or {})
    name = payload.get("investor", "") or payload.get("name", "")
    amount = payload.get("amount")
    note = payload.get("note", "")

    ok, record, status = deposits.add_pledge(name, amount, note)

    if request.is_json:
        if ok:
            return jsonify({"status": "success", "pledge": record}), 200
        code = 400 if status in ("invalid_investor", "invalid_amount", "amount_out_of_range") else 500
        return jsonify({"status": "error", "message": status}), code

    return redirect("/dashboard/investors")


@dashboard.route("/dashboard/investors/pledge/<pledge_id>/confirm", methods=["POST"])
@owner_required
def dashboard_investors_confirm_pledge(pledge_id: str):
    """Owner-only: mark a pledge as paid and record the matching deposit."""
    pledges = deposits.list_pledges_sorted()
    pledge = next((p for p in pledges if str(p.get("id")) == str(pledge_id)), None)
    if pledge is None:
        return jsonify({"status": "error", "message": "not_found"}), 404

    ok, _record, status = deposits.add_entry(
        "deposit",
        pledge.get("amount"),
        pledge.get("investor", ""),
        f"Confirmed investor pledge ({pledge.get('note') or 'no note'})",
    )
    if not ok:
        return jsonify({"status": "error", "message": status}), 400

    ok, record, status = deposits.set_pledge_status(pledge_id, "confirmed")
    if ok:
        return jsonify({"status": "success", "pledge": record}), 200
    return jsonify({"status": "error", "message": status}), 500


@dashboard.route("/dashboard/investors/pledge/<pledge_id>/decline", methods=["POST"])
@owner_required
def dashboard_investors_decline_pledge(pledge_id: str):
    """Owner-only: mark a pledge as declined/not followed up (no deposit added)."""
    ok, record, status = deposits.set_pledge_status(pledge_id, "declined")
    if ok:
        return jsonify({"status": "success", "pledge": record}), 200
    code = 404 if status == "not_found" else 400
    return jsonify({"status": "error", "message": status}), code


@dashboard.route("/dashboard/investors/pledge/<pledge_id>/delete", methods=["POST"])
@owner_required
def dashboard_investors_delete_pledge(pledge_id: str):
    """Owner-only: permanently remove a pledge row."""
    ok, status = deposits.delete_pledge(pledge_id)
    if ok:
        return jsonify({"status": "success"}), 200
    code = 404 if status == "not_found" else 400
    return jsonify({"status": "error", "message": status}), code


@dashboard.route("/dashboard/investors/tier", methods=["POST"])
@owner_required
def dashboard_investors_set_tier():
    """Owner-only: set an investor's ROI tier ("tradingview" or "full")."""
    payload = request.form if request.form else (request.get_json(silent=True) or {})
    name = payload.get("investor", "")
    tier = payload.get("tier", "")

    ok, status = deposits.set_investor_tier(name, tier)

    if request.is_json:
        if ok:
            return jsonify({"status": "success", "investor": name, "tier": tier}), 200
        code = 400 if status in ("invalid_investor", "invalid_tier") else 500
        return jsonify({"status": "error", "message": status}), code

    return redirect("/dashboard/investors")


@dashboard.route("/dashboard/analytics")
@login_required
def dashboard_analytics():
    """Render the in-depth Analytics page: best/worst trade, per-ticker
    win/loss breakdown, equity curve, and average hold time.

    Supports a ``?group=`` query param to break the analytics down by trade
    source: "tradingview" (TradingView + Hedge), "trader" (discretionary
    trades only), or "all" (default — current behaviour, every trade).

    Supports a ``?month=YYYY-MM`` query param to restrict the statistics to
    a single calendar month (e.g. September, October), based on each
    trade's exit time (falling back to entry time); omit/"all" for the
    full history (current behaviour)."""
    ctx = _build_request_context()
    group = str(request.args.get("group") or "all").strip().lower()
    if group not in ("all", "tradingview", "trader"):
        group = "all"

    if group == "all":
        group_trades = ctx["combined_trades"]
    else:
        sources = AUTOMATED_TRADE_SOURCES if group == "tradingview" else TRADER_TRADE_SOURCES
        group_trades = [t for t in ctx["combined_trades"] if _trade_type_label(t) in sources]

    available_months = _available_analytics_months(ctx["combined_trades"])
    month = str(request.args.get("month") or "all").strip().lower()
    if month != "all" and month not in available_months:
        month = "all"

    if month != "all":
        group_trades = _trades_in_month(group_trades, month)

    analytics = ctx["analytics"] if (group == "all" and month == "all") else _safe_analytics(compute_analytics(group_trades))
    detailed = compute_detailed_analytics(group_trades)

    return render_template(
        "analytics.html",
        title=getattr(config, "DASHBOARD_TITLE", "Dashboard"),
        cache_bust=time.time(),
        account=ctx["account"],
        analytics=analytics,
        detailed=detailed,
        group=group,
        month=month,
        available_months=available_months,
        is_owner=current_role() == "owner",
    )


@dashboard.route("/dashboard/transactions")
@owner_page_required
def dashboard_transactions():
    """Render the Owner-only transactions ledger: deposits, withdrawals,
    and manually-logged broker fees (e.g. Capital.com commission/financing
    charges).

    This is a manual ledger for tracking money moved in/out of the broker
    account (plus fees charged against it) for accurate equity context; it
    does not move real funds."""
    ctx = _build_request_context()
    entries = deposits.list_entries_sorted()
    totals = deposits.summarize(entries)
    investors = deposits.list_investors(entries)
    for e in entries:
        e["occurred_at_human"] = format_human(e.get("occurred_at")) or e.get("occurred_at")

    return render_template(
        "transactions.html",
        title=getattr(config, "DASHBOARD_TITLE", "Dashboard"),
        cache_bust=time.time(),
        account=ctx["account"],
        entries=entries,
        totals=totals,
        investors=investors,
        is_owner=True,
    )


@dashboard.route("/dashboard/deposits")
@owner_page_required
def dashboard_deposits():
    """Legacy URL redirect: the Deposits tab was renamed to Transactions
    (which now also tracks manually-logged broker fees)."""
    return redirect("/dashboard/transactions")


def _is_full_tier_investor(name, owner_name, tiers):
    """True if *name* is the Owner or has been granted "full" ROI tier
    (sharing in Trader/discretionary-trade PnL, not just TradingView/Hedge)."""
    name = str(name or "").strip()
    if owner_name and name.lower() == owner_name.strip().lower():
        return True
    return deposits.get_investor_tier(name, tiers) == "full"


def _build_roi_breakdown(entries, combined_trades, balance):
    """Compute per-investor ROI split into two sleeves:

    - A "TradingView" sleeve (TradingView + Hedge PnL) that every investor
      shares in pro-rata, with the Owner's configured performance fee.
    - A "Trader" sleeve (discretionary trade PnL) that only the Owner and
      "full" tier investors share in pro-rata, with no performance fee.

    Each investor's combined gain/loss/ROI is the sum of whichever sleeves
    they participate in; the two sleeves are reconciled against the real
    account balance once, combined, and any drift is credited to the Owner
    (consistent with the single-sleeve reconciliation behaviour)."""
    owner_name = str(getattr(config, "OWNER_INVESTOR_NAME", "Aleks") or "").strip()
    tiers = deposits.load_tiers()

    tv_pnl_events = _trade_pnl_events(combined_trades, sources=AUTOMATED_TRADE_SOURCES)
    trader_pnl_events = _trade_pnl_events(combined_trades, sources=TRADER_TRADE_SOURCES)

    # Manually-logged broker fees (Capital.com commission/financing charges)
    # are shared across every unit holder like any other NAV-moving event —
    # merged into the TradingView/Hedge sleeve only (which spans every
    # investor) so they aren't double-applied in the Trader sleeve below.
    fee_events = deposits.fee_pnl_events(entries)
    tv = deposits.investor_breakdown(entries, tv_pnl_events + fee_events, current_balance=None)

    full_tier_entries = [
        e for e in entries
        if _is_full_tier_investor(str((e or {}).get("investor") or "").strip() or "Unassigned", owner_name, tiers)
    ]
    trader = deposits.investor_breakdown(
        full_tier_entries, trader_pnl_events, current_balance=None, owner_override_pct_override=0,
    )

    owner_key = tv.get("owner_name")
    # Each sleeve's own "tracked_total" includes its OWN capital flows plus
    # its PnL — but both sleeves share the SAME underlying capital (the
    # same real deposits), just exposed to two different PnL streams.
    # Summing the two tracked_totals directly would double-count full-tier
    # investors' capital. Instead, combine capital once (from the TV sleeve,
    # which spans every investor) and add each sleeve's PnL delta on top.
    tv_pnl_delta = (tv.get("tracked_total") or 0.0) - (tv.get("total_net_contribution") or 0.0)
    trader_pnl_delta = (trader.get("tracked_total") or 0.0) - (trader.get("total_net_contribution") or 0.0)
    combined_tracked_total = round(
        (tv.get("total_net_contribution") or 0.0) + tv_pnl_delta + trader_pnl_delta, 2
    )
    reconciliation_adjustment = (
        round(balance - combined_tracked_total, 2) if balance is not None and owner_key else 0.0
    )

    trader_by_name = {inv["investor"].strip().lower(): inv for inv in trader.get("investors", [])}

    investors_out = []
    for inv in tv.get("investors", []):
        name = inv["investor"]
        is_full = bool(inv.get("is_owner")) or _is_full_tier_investor(name, owner_name, tiers)
        tier = "owner" if inv.get("is_owner") else ("full" if is_full else "tradingview")

        trader_inv = trader_by_name.get(name.strip().lower())
        trader_gain = round(trader_inv["allocated_gain_loss"], 2) if trader_inv and trader_inv.get("allocated_gain_loss") is not None else 0.0
        owner_bonus = reconciliation_adjustment if inv.get("is_owner") else 0.0

        net_contribution = inv["net_contribution"]
        combined_gain_loss = round(inv["allocated_gain_loss"] + trader_gain + owner_bonus, 2)
        combined_roi_pct = (
            round((combined_gain_loss / net_contribution) * 100, 2)
            if net_contribution not in (0, 0.0) else None
        )

        row = dict(inv)
        row["tier"] = tier
        row["tv_allocated_gain_loss"] = inv["allocated_gain_loss"]
        row["trader_allocated_gain_loss"] = trader_gain if is_full else None
        row["allocated_gain_loss"] = combined_gain_loss
        row["current_value"] = round(inv["current_value"] + trader_gain + owner_bonus, 2)
        row["roi_pct"] = combined_roi_pct
        investors_out.append(row)

    total_net_contribution = tv.get("total_net_contribution")
    overall_gain_loss = (
        round(balance - total_net_contribution, 2)
        if balance is not None and total_net_contribution is not None
        else None
    )
    overall_roi_pct = (
        round((overall_gain_loss / total_net_contribution) * 100, 2)
        if overall_gain_loss is not None and total_net_contribution not in (0, 0.0)
        else None
    )

    return {
        "investors": investors_out,
        "total_net_contribution": total_net_contribution,
        "current_balance": balance,
        "overall_gain_loss": overall_gain_loss,
        "overall_roi_pct": overall_roi_pct,
        "owner_name": owner_key,
        "owner_override_pct": tv.get("owner_override_pct"),
        "pre_ledger_pnl": tv.get("pre_ledger_pnl"),
        "trader_pre_ledger_pnl": trader.get("pre_ledger_pnl"),
        "reconciliation_adjustment": reconciliation_adjustment if owner_key else None,
        # Total manually-logged broker fees (Capital.com commission/financing
        # charges) already factored into overall_gain_loss above via the
        # real account balance, and shared across investors' allocated
        # gain/loss via the NAV-per-unit timeline — shown here for
        # transparency on the ROI page.
        "total_fees": deposits.summarize(entries).get("total_fees", 0.0),
    }


@dashboard.route("/dashboard/roi")
@roi_access_required
def dashboard_roi():
    """Render the ROI page (Owner and Investor roles only): per-investor
    ownership share and gain/loss, computed via NAV-per-unit accounting
    against the chronological deposit/withdrawal and trade-PnL timeline. An
    investor only participates in gains/losses from trades closed after
    their own deposit, and only in Trader-trade PnL if they have been
    granted "full" ROI tier."""
    ctx = _build_request_context()
    entries = deposits.list_entries_sorted()
    account = ctx["account"]
    breakdown = _build_roi_breakdown(entries, ctx["combined_trades"], (account or {}).get("balance"))

    return render_template(
        "roi.html",
        title=getattr(config, "DASHBOARD_TITLE", "Dashboard"),
        cache_bust=time.time(),
        account=account,
        breakdown=breakdown,
        is_owner=current_role() == "owner",
        role=current_role(),
    )


@dashboard.route("/dashboard/transactions/add", methods=["POST"])
@owner_required
def dashboard_transactions_add():
    """Add a manual deposit/withdrawal/fee row to the bookkeeping ledger."""
    payload = request.form if request.form else (request.get_json(silent=True) or {})
    entry_type = payload.get("type")
    amount = payload.get("amount")
    investor = payload.get("investor", "")
    note = payload.get("note", "")
    occurred_at = payload.get("occurred_at")

    ok, record, status = deposits.add_entry(entry_type, amount, investor, note, occurred_at)

    if request.is_json:
        if ok:
            return jsonify({"status": "success", "entry": record}), 200
        code = 400 if status in ("invalid_type", "invalid_amount", "invalid_investor") else 500
        return jsonify({"status": "error", "message": status}), code

    # Standard HTML form submission: redirect back to the page.
    return redirect("/dashboard/transactions")


@dashboard.route("/dashboard/transactions/<entry_id>/delete", methods=["POST"])
@owner_required
def dashboard_transactions_delete(entry_id: str):
    """Delete a row from the deposit/withdrawal/fee bookkeeping ledger."""
    ok, status = deposits.delete_entry(entry_id)

    if ok:
        return jsonify({"status": "success", "message": "Entry deleted."}), 200

    code = 404 if status == "not_found" else (400 if status == "invalid_id" else 500)
    return jsonify({"status": "error", "message": status}), code


@dashboard.route("/dashboard/notifications/email/toggle", methods=["POST"])
@owner_required
def dashboard_toggle_email_notifications():
    """Owner-only: turn the "send to email" position notifications on/off.

    The first time it's switched on with no recipient configured anywhere,
    the recipient defaults to notifications.DEFAULT_RECIPIENT_EMAIL.
    """
    payload = request.get_json(silent=True) or request.form or {}
    enabled = payload.get("enabled")
    if enabled is None:
        # No explicit value supplied – flip the current state.
        enabled = not notifications.is_enabled()
    else:
        enabled = str(enabled).strip().lower() in ("1", "true", "yes", "on")

    settings = notifications.set_enabled(enabled)
    return jsonify({
        "status": "success",
        "enabled": bool(settings.get("enabled")),
        "recipient": settings.get("to") or notifications.get_recipient(),
    }), 200


@dashboard.route("/dashboard/close/<position_id>", methods=["POST"])
@owner_required
def dashboard_close_position(position_id: str):
    """Close a live broker position from the dashboard."""
    position_id = str(position_id or "").strip()
    if not position_id:
        return jsonify({
            "status": "error",
            "message": "missing_position_id",
        }), 400

    try:
        result = close_live_position(position_id)
    except Exception as exc:
        logger.exception("dashboard: close action failed for %s: %s", position_id, exc)
        return jsonify({
            "status": "error",
            "message": "close_exception",
            "detail": "close_failed_internal",
        }), 500

    if isinstance(result, dict) and result.get("status") == "success":
        payload = {
            "status": "success",
            "message": f"Position {position_id} closed.",
        }
        if result.get("warning"):
            payload["warning"] = "trade_log_update_failed"
        return jsonify(payload), 200

    if isinstance(result, dict):
        return jsonify({
            "status": "error",
            "message": "close_failed",
        }), 502

    return jsonify({
        "status": "error",
        "message": "invalid_close_response",
    }), 502


@dashboard.route("/dashboard/trade/<int:trade_index>/delete", methods=["POST"])
@owner_required
def dashboard_delete_trade(trade_index: int):
    """Delete one completed or broker-missing phantom trade-log row from the dashboard."""
    try:
        deleted, _trade, status = delete_trade_log_entry(trade_index)
    except Exception as exc:
        logger.exception("dashboard: delete action failed for trade %s: %s", trade_index, exc)
        return jsonify({
            "status": "error",
            "message": "delete_failed_internal",
        }), 500

    if deleted:
        return jsonify({
            "status": "success",
            "message": "Trade deleted from the log.",
        }), 200

    if status == "invalid_index":
        code = 400
    elif status == "not_found":
        code = 404
    elif status in ("not_completed", "not_deletable", "broker_still_open"):
        code = 409
    else:
        code = 500
    return jsonify({
        "status": "error",
        "message": status,
    }), code


@dashboard.route("/dashboard/trade/<int:trade_index>/type", methods=["POST"])
@owner_required
def dashboard_update_trade_type(trade_index: int):
    """Correct a trade log row's recorded type (e.g. mislabeled 'Trader' rows
    that were really TradingView alerts), so analytics stay accurate."""
    payload = request.get_json(silent=True) or {}
    new_type = payload.get("type")

    try:
        updated, _trade, status = update_trade_type_entry(trade_index, new_type)
    except Exception as exc:
        logger.exception("dashboard: update type action failed for trade %s: %s", trade_index, exc)
        return jsonify({
            "status": "error",
            "message": "update_type_failed_internal",
        }), 500

    if updated:
        return jsonify({
            "status": "success",
            "message": "Trade type updated.",
        }), 200

    if status == "invalid_index":
        code = 400
    elif status == "invalid_type":
        code = 400
    elif status == "not_found":
        code = 404
    else:
        code = 500
    return jsonify({
        "status": "error",
        "message": status,
    }), code


@dashboard.route("/dashboard/trade/<int:trade_index>/exit_price", methods=["POST"])
@owner_required
def dashboard_update_trade_exit_price(trade_index: int):
    """Manually set/correct a completed trade's exit price.

    PnL (and PnL GBP) is recalculated automatically from the new exit price,
    covering trades where the bot never recorded one (e.g. a sharp spike hit
    the broker's own SL/TP between polling ticks)."""
    payload = request.get_json(silent=True) or {}
    new_exit_price = payload.get("exit_price")

    try:
        updated, _trade, status = update_trade_exit_price_entry(trade_index, new_exit_price)
    except Exception as exc:
        logger.exception("dashboard: update exit price action failed for trade %s: %s", trade_index, exc)
        return jsonify({
            "status": "error",
            "message": "update_exit_price_failed_internal",
        }), 500

    if updated:
        return jsonify({
            "status": "success",
            "message": "Exit price updated.",
        }), 200

    if status in ("invalid_index", "invalid_price"):
        code = 400
    elif status == "not_found":
        code = 404
    elif status == "not_completed":
        code = 409
    else:
        code = 500
    return jsonify({
        "status": "error",
        "message": status,
    }), code


# -----------------------------
# Module test harness
# -----------------------------
if __name__ == "__main__":
    # quick local smoke test
    print("Dashboard module quick smoke test")
    try:
        trades = load_raw_log()
        print("Loaded trades:", len(trades))
        norm = normalize_trades(trades)
        dedup = dedupe_trades(norm)
        print("Normalized:", len(norm), "Deduped:", len(dedup))
        analytics = compute_analytics(filter_completed(dedup))
        print("Analytics:", analytics)
    except Exception as e:
        print("Smoke test failed:", e)
