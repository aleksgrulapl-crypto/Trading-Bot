# ============================
# DASHBOARD MODULE (FINAL CLEAN)
# ============================

import json
import functools
from flask import Blueprint, request, render_template, redirect, jsonify

import session
import config
from trade_log import load_log

dashboard = Blueprint("dashboard", __name__)

# ---------------------------------------------------------
# LOGIN REQUIRED DECORATOR
# ---------------------------------------------------------

def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not request.cookies.get("dashboard_auth"):
            return redirect("/dashboard/login")
        return view(*args, **kwargs)
    return wrapper

# ---------------------------------------------------------
# LOGIN PAGE
# ---------------------------------------------------------

@dashboard.route("/dashboard/login", methods=["GET", "POST"])
def dashboard_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == config.DASHBOARD_PASSWORD:
            resp = redirect("/dashboard")
            resp.set_cookie("dashboard_auth", "1", max_age=86400)
            return resp
        return render_template("login.html", error="Incorrect password")
    return render_template("login.html", error=None)

# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------

@dashboard.route("/dashboard/logout")
def dashboard_logout():
    resp = redirect("/dashboard/login")
    resp.delete_cookie("dashboard_auth")
    return resp

# ---------------------------------------------------------
# LOAD TREND LOGS
# ---------------------------------------------------------

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

# ---------------------------------------------------------
# DASHBOARD HOME
# ---------------------------------------------------------

@dashboard.route("/dashboard")
@login_required
def dashboard_home():

    # Fetch raw data
    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    # Enrich data
    positions = session.enrich_positions(raw_positions)
    account = session.enrich_account(raw_account)

    # Update shared state
    session.shared_state["account"] = account
    session.shared_state["positions"] = positions
    session.shared_state["trade_log"] = load_log()

    # Trend logs
    available_log = load_available_log()
    equity_log = load_equity_log()

    return render_template(
        "dashboard.html",
        title=config.DASHBOARD_TITLE,
        account=account,
        positions=positions,
        trade_log=session.shared_state["trade_log"],
        daily_report=session.shared_state.get("daily_report", {}),
        system_status=session.shared_state.get("system_status", {}),
        available_log=available_log,
        equity_log=equity_log
    )

# ---------------------------------------------------------
# DASHBOARD PARTIAL (AJAX REFRESH)
# ---------------------------------------------------------

@dashboard.route("/dashboard/data")
@login_required
def dashboard_data():

    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    positions = session.enrich_positions(raw_positions)
    account = session.enrich_account(raw_account)

    session.shared_state["account"] = account
    session.shared_state["positions"] = positions
    session.shared_state["trade_log"] = load_log()

    available_log = load_available_log()
    equity_log = load_equity_log()

    html = render_template(
        "dashboard_partial.html",
        account=account,
        positions=positions,
        trade_log=session.shared_state["trade_log"],
        daily_report=session.shared_state.get("daily_report", {}),
        system_status=session.shared_state.get("system_status", {}),
        available_log=available_log,
        equity_log=equity_log
    )

    return jsonify({"html": html})

# ---------------------------------------------------------
# CLOSE POSITION
# ---------------------------------------------------------

@dashboard.route("/dashboard/close/<position_id>")
@login_required
def dashboard_close(position_id):
    from close_position import close_position

    close_position(position_id)

    # Refresh state
    raw_positions = session.get_positions() or []
    raw_account = session.get_account() or {}

    session.shared_state["positions"] = session.enrich_positions(raw_positions)
    session.shared_state["account"] = session.enrich_account(raw_account)
    session.shared_state["trade_log"] = load_log()

    return redirect("/dashboard")

# ---------------------------------------------------------
# DEBUG
# ---------------------------------------------------------

@dashboard.route("/dashboard/debug")
@login_required
def dashboard_debug():
    return jsonify({
        "account": session.shared_state.get("account"),
        "positions": session.shared_state.get("positions"),
        "trade_log": session.shared_state.get("trade_log"),
        "system_status": session.shared_state.get("system_status", {}),
        "daily_report": session.shared_state.get("daily_report", {}),
        "available_log": load_available_log(),
        "equity_log": load_equity_log()
    })
