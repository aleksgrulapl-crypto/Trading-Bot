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
import logging
import os
from flask import Blueprint, request, render_template, redirect, jsonify

import session
import config
from close_position import close_position as close_live_position
from trade_log import (
    load_raw_log,
    reconcile_with_positions,
    get_completed_trades,
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
    password = request.form.get("password", "")
    expected = os.getenv("DASHBOARD_PASSWORD", getattr(config, "DASHBOARD_PASSWORD", None) or "Angelika140282")
    if expected == "Angelika140282":
        logger.warning("Using default dashboard password. Set DASHBOARD_PASSWORD in environment to secure the dashboard.")
    if password == expected:
        resp = redirect("/dashboard")
        resp.set_cookie("dashboard_auth", "1", max_age=60 * 60 * 24 * 7)
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


# -----------------------------
# Helpers: normalization, dedupe, filtering
# -----------------------------
def _safe_str(v):
    return str(v) if v is not None else None


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

        # human timestamps preserved by trade_log but ensure keys exist
        copy["time_entered"] = copy.get("time_entered")
        copy["time_exited"] = copy.get("time_exited")
        copy["time_entered_human"] = copy.get("time_entered_human")
        copy["time_exited_human"] = copy.get("time_exited_human")

        out.append(copy)
    return out


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
    Deduplicate trades. Prefer CLOSED records over OPEN when duplicates found.
    """
    seen = {}
    unique = []
    for t in trades or []:
        sig = _signature_for_dedupe(t)
        idx = seen.get(sig)
        if idx is None:
            seen[sig] = len(unique)
            unique.append(t)
        else:
            existing = unique[idx]
            # prefer closed over open
            if existing.get("status") != "CLOSED" and t.get("status") == "CLOSED":
                unique[idx] = t
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

    # copy and coerce pnl to numeric where possible
    cleaned = []
    for t in trades:
        copy = dict(t)
        pnl_raw = copy.get("pnl", None)
        try:
            copy["pnl"] = float(pnl_raw) if pnl_raw not in (None, "") else None
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

    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    positions = session.enrich_positions(raw_positions)
    account = session.enrich_account(raw_account)

    # Reconcile local trade log against live positions (may update the log file)
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
        reverse=True,
    )

    analytics = _safe_analytics(compute_analytics(filter_completed(combined_trades)))

    return {
        "account": account,
        "positions": positions,
        "combined_trades": combined_trades,
        "analytics": analytics,
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
        trades=ctx["combined_trades"],
        analytics=ctx["analytics"],
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
            trades=ctx["combined_trades"],
            analytics=ctx["analytics"],
        )
        return jsonify({
            "html": html,
            "account": ctx["account"],
            "positions": ctx["positions"],
            "trades": ctx["combined_trades"],
            "analytics": ctx["analytics"],
        })
    except Exception as exc:
        logger.exception("dashboard/data render failed: %s", exc)
        return jsonify({
            "error": "render_failed",
            "message": "Failed to render dashboard partial",
            "details": str(exc),
        }), 500


@dashboard.route("/dashboard/close/<position_id>", methods=["POST"])
@login_required
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
            "detail": str(exc),
        }), 500

    if isinstance(result, dict) and result.get("status") == "success":
        return jsonify(result), 200

    if isinstance(result, dict):
        return jsonify(result), 502

    return jsonify({
        "status": "error",
        "message": "invalid_close_response",
    }), 502


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
