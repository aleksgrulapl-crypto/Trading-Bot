# ============================
# WEBHOOK MODULE (FINAL CLEAN)
# ============================

from flask import Flask, request, jsonify, render_template
from scheduler import start_scheduler
from dashboard import dashboard as dashboard_blueprint

import session
import order
import utils
from trade_log import load_log
from auth import auth
from config import API_POSITIONS, API_ACCOUNTS
from parser import parse_tradingview_alert

app = Flask(__name__)
app.register_blueprint(dashboard_blueprint)

# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------

print("[System] Starting scheduler...")
start_scheduler()
print("[System] Scheduler started.")

# ---------------------------------------------------------
# RAW DEBUG ENDPOINTS
# ---------------------------------------------------------

@app.route("/raw")
def raw_positions():
    raw = session.get_positions()
    return jsonify(raw)

@app.route("/raw/account")
def raw_account():
    raw = session.get_account()
    return jsonify(raw)

# ---------------------------------------------------------
# WEBHOOK ENDPOINT
# ---------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    raw_body = request.get_data(as_text=True).strip()

    if not raw_body:
        return jsonify({"status": "error", "message": "Empty body"}), 200

    # -----------------------------------------------------
    # Parse RAW or JSON alert
    # -----------------------------------------------------
    try:
        if "|" in raw_body:
            alert = parse_tradingview_alert(raw_body)
        else:
            json_data = request.get_json(force=True)
            alert = parse_tradingview_alert(json_data)
    except Exception as e:
        print(f"[Webhook] Parse error: {e}")
        return jsonify({"status": "error", "message": "Invalid alert"}), 200

    ticker = alert["symbol"]
    action = alert["action"]
    size = alert["quantity"]
    sl = alert.get("sl")
    tp = alert.get("tp")

    print(f"[Webhook] Alert → {ticker} {action.upper()} size={size} SL={sl} TP={tp}")

    # -----------------------------------------------------
    # EPIC lookup
    # -----------------------------------------------------
    epic_data = session.verify_epic(ticker)
    epic = epic_data.get("epic")

    if not epic:
        print(f"[Webhook] EPIC lookup failed for {ticker}")
        return jsonify({"status": "error", "message": "EPIC lookup failed"}), 200

    print(f"[Webhook] EPIC resolved → {epic}")

    # -----------------------------------------------------
    # Place order
    # -----------------------------------------------------
    result = order.place_order(epic, action, size, sl, tp)

    return jsonify({"status": "ok", "result": result}), 200

# ---------------------------------------------------------
# DASHBOARD ROUTES
# ---------------------------------------------------------

@app.route("/")
@app.route("/dashboard")
def dashboard():
    raw_positions = session.get_positions() or []
    positions = session.enrich_positions(raw_positions)

    raw_account = session.get_account() or {}
    account = session.enrich_account(raw_account)

    trade_log = load_log()
    daily_report = session.shared_state.get("daily_report", {})

    return render_template(
        "dashboard.html",
        account=account,
        positions=positions,
        trade_log=trade_log,
        daily_report=daily_report,
        system_status=session.shared_state.get("system_status", {})
    )

# ---------------------------------------------------------
# CLOSE POSITION (API)
# ---------------------------------------------------------

@app.route("/close/<position_id>", methods=["POST"])
def close_position_api(position_id):
    from close_position import close_position

    result = close_position(position_id)
    return jsonify(result)

# ---------------------------------------------------------
# RUN APP
# ---------------------------------------------------------

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
