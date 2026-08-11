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
# LOAD AVAILABLE BALANCE LOG
# ---------------------------------------------------------

def load_available_log():
    data = []
    try:
        with open("available_log.json") as f:
            for line in f:
                data.append(json.loads(line))
    except:
        pass
    return data


# ---------------------------------------------------------
# DASHBOARD HOME
# ---------------------------------------------------------

@dashboard.route("/dashboard")
@login_required
def dashboard_home():

    # Safely refresh account + positions
    account = session.get_account() or {}
    positions = session.get_positions() or []

    # Update shared state safely
    session.shared_state["account"] = account
    session.shared_state["positions"] = positions

    # Load available balance trend data
    available_log = load_available_log()

    return render_template(
        "dashboard.html",
        title=config.DASHBOARD_TITLE,
        account=account,
        positions=positions,
        trade_log=load_log(),
        daily_report=session.shared_state.get("daily_report", {}),
        system_status=session.shared_state.get("system_status", {}),
        available_log=available_log
    )


# ---------------------------------------------------------
# DASHBOARD PARTIAL (AJAX REFRESH)
# ---------------------------------------------------------

@dashboard.route("/dashboard/data")
@login_required
def dashboard_data():

    account = session.get_account() or {}
    positions = session.get_positions() or []

    session.shared_state["account"] = account
    session.shared_state["positions"] = positions

    available_log = load_available_log()

    html = render_template(
        "dashboard_partial.html",
        account=account,
        positions=positions,
        trade_log=load_log(),
        daily_report=session.shared_state.get("daily_report", {}),
        system_status=session.shared_state.get("system_status", {}),
        available_log=available_log
    )

    return jsonify({"html": html})


# ---------------------------------------------------------
# CLOSE POSITION
# ---------------------------------------------------------

@dashboard.route("/dashboard/close/<position_id>")
@login_required
def dashboard_close(position_id):
    from close_position import close_position

    # Execute close
    close_position(position_id)

    # Refresh positions safely
    session.shared_state["positions"] = session.get_positions() or []

    return redirect("/dashboard")


# ---------------------------------------------------------
# DEBUG
# ---------------------------------------------------------

@dashboard.route("/dashboard/debug")
@login_required
def dashboard_debug():
    return jsonify({
        "account": session.get_account(),
        "positions": session.get_positions(),
        "trade_log": load_log(),
        "system_status": session.shared_state.get("system_status", {}),
        "daily_report": session.shared_state.get("daily_report", {}),
        "available_log": load_available_log()
    })
