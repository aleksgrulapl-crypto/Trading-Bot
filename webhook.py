from flask import Flask, request, jsonify, render_template
from scheduler import start_scheduler
from dashboard import dashboard as dashboard_blueprint
import session
import order
import utils
import report
from trade_log import load_log
from auth import auth

app = Flask(__name__)
app.register_blueprint(dashboard_blueprint)


# ---------------------------------------------------------
# STARTUP
# ---------------------------------------------------------

print("[Webhook] Starting scheduler...")
start_scheduler()
print("[Webhook] Scheduler started.")


# ---------------------------------------------------------
# WEBHOOK ENDPOINT
# ---------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    session.update_last_webhook()

    if not data:
        return jsonify({"status": "error", "message": "No JSON received"})

    symbol = data.get("symbol")
    action = data.get("action")
    size = float(data.get("size", 1))
    price = data.get("price")
    sl = data.get("sl")
    tp = data.get("tp")

    print(f"[Webhook] Received: {symbol} {action} {size} @ {price}")

    result = order.place_order(symbol, action, size, sl, tp)
    return jsonify({"status": "ok", "result": result})


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
    daily_report = session.get_daily_report() or {}

    return render_template(
        "dashboard.html",
        account=account,
        positions=positions,
        trade_log=trade_log,
        daily_report=daily_report,
        system_status=session.shared_state.get("system_status", {})
    )


# ---------------------------------------------------------
# CLOSE POSITION
# ---------------------------------------------------------

@app.route("/close/<position_id>", methods=["POST"])
def close_position(position_id):
    auth.ensure_token()

    payload = {
        "dealId": position_id,
        "direction": "SELL",
        "size": 0
    }

    r = session.request("POST", f"{API_POSITIONS}/{position_id}/close", json=payload)
    result = r.json()

    for p in session.shared_state.get("positions", []):
        if str(p["id"]) == str(position_id):
            utils.log_trade(
                ticker=p["ticker"],
                side="CLOSE",
                size=p["size"],
                price=p["current_price"],
                pnl=p["profit"],
                timestamp=utils.timestamp()
            )

    session.update_last_trade()
    return jsonify({"status": "ok", "result": result})


# ---------------------------------------------------------
# MANUAL DAILY REPORT
# ---------------------------------------------------------

@app.route("/daily-report", methods=["POST"])
def manual_daily_report():
    report_data = report.generate_daily_report()
    return jsonify(report_data)


# ---------------------------------------------------------
# RUN APP
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
