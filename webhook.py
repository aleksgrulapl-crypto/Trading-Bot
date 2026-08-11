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

print("[Webhook] Starting scheduler...")
start_scheduler()
print("[Webhook] Scheduler started.")

@app.route("/raw")
def raw_positions():
    return jsonify(session.fetch_positions_from(API_POSITIONS))

@app.route("/raw/account")
def raw_account():
    return jsonify(session.request("GET", API_ACCOUNTS).json())

@app.route("/webhook", methods=["POST"])
def webhook():
    session.update_last_webhook()

    raw = request.get_data(as_text=True)
    if not raw.strip():
        return jsonify({"status": "error", "message": "Empty body received"}), 200

    if "|" in raw:
        alert = parse_tradingview_alert(raw)
    else:
        data = request.get_json(force=True)
        alert = parse_tradingview_alert(data)

    ticker = alert["symbol"]
    action = alert["action"]
    size = alert["quantity"]
    sl = alert.get("sl")
    tp = alert.get("tp")

    epic_data = session.verify_epic(ticker)
    epic = epic_data.get("epic")

    result = order.place_order(epic, action, size, sl, tp)

    return jsonify({"status": "ok", "result": result}), 200

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

@app.route("/close/<position_id>", methods=["POST"])
def close_position(position_id):
    auth.ensure_token()
    payload = {"dealId": position_id, "direction": "SELL", "size": 0}
    r = session.request("POST", f"{API_POSITIONS}/{position_id}/close", json=payload)
    result = r.json()
    session.update_last_trade()
    return jsonify({"status": "ok", "result": result})

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
