# ============================
# DASHBOARD MODULE (FULLY CORRECTED)
# ============================

import json
import functools
import time
from flask import Blueprint, request, render_template, redirect, jsonify

import session
import config
from trade_log import load_log
from excel_import import load_excel_trades

dashboard = Blueprint("dashboard", __name__, template_folder="templates")


# -----------------------------
# AUTH
# -----------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        return view(*args, **kwargs)
    return wrapper


# -----------------------------
# TRADE NORMALIZATION
# -----------------------------
def normalize_trades(trades):
    normalized = []

    for t in trades:
        # unify dealId / dealReference / trade_id
        deal_id = (
            t.get("dealId")
            or t.get("deal_id")
            or t.get("trade_id")
            or t.get("dealReference")
            or t.get("reference")
        )

        t["dealId"] = str(deal_id) if deal_id is not None else "None"

        t.setdefault("open_timestamp", t.get("time"))
        t.setdefault("close_timestamp", t.get("time"))

        if not t.get("ticker"):
            t["ticker"] = t.get("epic") or t.get("symbol") or "—"

        side = t.get("side")
        if isinstance(side, str):
            t["side"] = side.upper()
        else:
            t["side"] = "SELL"

        t.setdefault("size", t.get("size") or "—")
        t.setdefault("entry_price", t.get("entry_price") or "—")
        t.setdefault("exit_price", t.get("exit_price") or "—")

        pnl = t.get("pnl", 0)
        try:
            t["pnl"] = float(pnl)
        except Exception:
            t["pnl"] = 0.0

        t.setdefault("checklist_passed", "Yes")
        t.setdefault("notes", "")

        normalized.append(t)

    return normalized


def dedupe_trades(trades):
    seen = set()
    unique = []

    for i, t in enumerate(trades):
        # use unified dealId
        key = (
            str(t.get("dealId")),
            str(t.get("close_timestamp") or t.get("time")),
        )

        if key == ("None", "None"):
            key = f"fallback_{i}"

        if key not in seen:
            seen.add(key)
            unique.append(t)

    return unique


def filter_completed(trades):
    return [
        t for t in trades
        if t.get("status") == "CLOSED" or t.get("exit_price") not in (None, "—")
    ]


# -----------------------------
# ANALYTICS ENGINE
# -----------------------------
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
        pnl = t.get("pnl")
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            pnl = 0.0
        t["pnl"] = pnl
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

    cumulative = []
    running = 0
    max_peak = 0
    max_drawdown = 0

    for t in trades:
        running += t["pnl"]
        cumulative.append(running)
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


# -----------------------------
# LOG LOADERS
# -----------------------------
def load_available_log():
    try:
        with open("available_log.json") as f:
            return [json.loads(line) for line in f]
    except:
        return []


def load_equity_log():
    try:
        with open("equity_log.json") as f:
            return [json.loads(line) for line in f]
    except:
        return []


def clean_value(v):
    try:
        if v is None:
            return None
        if isinstance(v, float) and (v != v):  # NaN
            return None
        return v
    except:
        return None


def clean_structure(obj):
    if isinstance(obj, dict):
        return {k: clean_structure(clean_value(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_structure(clean_value(x)) for x in obj]
    return clean_value(obj)


# -----------------------------
# LOGIN
# -----------------------------
@dashboard.route("/dashboard/login", methods=["GET", "POST"])
def dashboard_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == config.DASHBOARD_PASSWORD:
            resp = redirect("/dashboard")
            resp.set_cookie("dashboard_auth", "1", max_age=86400)
            return resp
        return render_template("login.html", error="Incorrect password", cache_bust=time.time())
    return render_template("login.html", error=None, cache_bust=time.time())


@dashboard.route("/dashboard/logout")
def dashboard_logout():
    resp = redirect("/dashboard/login")
    resp.delete_cookie("dashboard_auth")
    return resp


# -----------------------------
# MAIN DASHBOARD PAGE
# -----------------------------
@dashboard.route("/dashboard")
@login_required
def dashboard_home():
    session._cache["account"]["ts"] = 0

    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    positions = session.enrich_positions(raw_positions)
    account = session.enrich_account(raw_account)

    # LIVE EQUITY
    try:
        open_pnl = sum(p.get("pnl", 0) or 0 for p in positions)
        if account.get("balance") is not None:
            account["balance"] = round(account["balance"] + open_pnl, 2)
    except Exception as e:
        print(f"[DASHBOARD] live equity calc failed: {e}", flush=True)

    capital_trades = load_log()
    excel_trades = load_excel_trades()
    combined_raw = capital_trades + excel_trades
    combined_trades = filter_completed(normalize_trades(dedupe_trades(combined_raw)))

    daily_report = session.get_daily_report()
    available_log = load_available_log()
    equity_log = load_equity_log()

    analytics = compute_analytics(combined_trades)

    # Update shared state
    session.shared_state["account"] = account
    session.shared_state["positions"] = positions
    session.shared_state["trade_log"] = combined_trades
    session.shared_state["daily_report"] = daily_report
    session.shared_state["analytics"] = analytics

    return render_template(
        "dashboard.html",
        title=config.DASHBOARD_TITLE,
        cache_bust=time.time(),
        account=account,
        positions=positions,
        trades=combined_trades,
        daily_report=daily_report,
        system_status=session.shared_state.get("system_status", {}),
        available_log=available_log,
        equity_log=equity_log,
        analytics=analytics
    )


# -----------------------------
# AJAX REFRESH ENDPOINT (FULLY FIXED)
# -----------------------------
@dashboard.route("/dashboard/data")
@login_required
def dashboard_data():
    session._cache["account"]["ts"] = 0

    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    positions = session.enrich_positions(raw_positions)
    account = session.enrich_account(raw_account)

    # LIVE EQUITY
    try:
        open_pnl = sum(p.get("pnl", 0) or 0 for p in positions)
        if account.get("balance") is not None:
            account["balance"] = round(account["balance"] + open_pnl, 2)
    except Exception as e:
        print(f"[DASHBOARD] live equity calc failed (data): {e}", flush=True)

    capital_trades = load_log()
    excel_trades = load_excel_trades()
    combined_raw = capital_trades + excel_trades
    combined_trades = filter_completed(normalize_trades(dedupe_trades(combined_raw)))

    daily_report = session.get_daily_report()
    available_log = load_available_log()
    equity_log = load_equity_log()

    analytics = compute_analytics(combined_trades)

    # update shared_state BEFORE rendering
    session.shared_state["account"] = account
    session.shared_state["positions"] = positions
    session.shared_state["trade_log"] = combined_trades
    session.shared_state["daily_report"] = daily_report
    session.shared_state["analytics"] = analytics

    html = render_template(
        "dashboard_partial.html",
        cache_bust=time.time(),
        account=account,
        positions=positions,
        trades=combined_trades,
        daily_report=daily_report,
        system_status=session.shared_state.get("system_status", {}),
        available_log=available_log,
        equity_log=equity_log,
        analytics=analytics
    )

    return jsonify(clean_structure({
        "html": html,
        "account": account,
        "positions": positions,
        "trades": combined_trades,
        "available_log": available_log,
        "equity_log": equity_log,
        "daily_report": daily_report,
        "analytics": analytics
    }))


# -----------------------------
# CLOSE POSITION
# -----------------------------
@dashboard.route("/dashboard/close/<position_id>")
@login_required
def dashboard_close(position_id):
    from close_position import close_position

    close_position(position_id)

    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    session.shared_state["positions"] = session.enrich_positions(raw_positions)
    session.shared_state["account"] = session.enrich_account(raw_account)
    session.shared_state["trade_log"] = load_log()

    return redirect("/dashboard")


# -----------------------------
# DEBUG ENDPOINT
# -----------------------------
@dashboard.route("/dashboard/debug")
@login_required
def dashboard_debug():
    return jsonify(clean_structure({
        "account": session.shared_state.get("account"),
        "positions": session.shared_state.get("positions"),
        "trade_log": session.shared_state.get("trade_log"),
        "system_status": session.shared_state.get("system_status", {}),
        "daily_report": session.shared_state.get("daily_report", {}),
        "available_log": load_available_log(),
        "equity_log": load_equity_log()
    }))
