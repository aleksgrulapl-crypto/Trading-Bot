# ============================
# WEBHOOK MODULE (FULLY TOLERANT VERSION)
# ============================

from flask import Flask, request, jsonify, render_template
from scheduler import start_scheduler
from dashboard import dashboard as dashboard_blueprint

import session
import order
from trade_log import load_log
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
    return jsonify(session.get_positions())

@app.route("/raw/account")
def raw_account():
    return jsonify(session.get_account())

# ---------------------------------------------------------
# WEBHOOK ENDPOINT (FULLY TOLERANT)
# ---------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    raw_body = request.get_data(as_text=True).strip()
    json_body = request.get_json(silent=True)

    # Reject empty body
    if not raw_body and not json_body:
        return jsonify({"status": "error", "message": "Empty alert"}), 200

    # -----------------------------------------------------
    # Decide RAW vs JSON automatically
    # -----------------------------------------------------
    try:
        if raw_body and "|" in raw_body:
            # RAW alert
            alert = parse_tradingview_alert(raw_body)

        elif json_body:
            # JSON alert
            alert = parse_tradingview_alert(json_body)

        else:
            # Fallback: treat raw_body as RAW even without "|"
            alert = parse_tradingview_alert(raw_body)

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
    return jsonify(close_position(position_id))

# ---------------------------------------------------------
# RUN APP
# ---------------------------------------------------------

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
